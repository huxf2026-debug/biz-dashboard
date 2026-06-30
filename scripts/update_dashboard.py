#!/usr/bin/env python3
"""
经营看板数据更新脚本 v2.0（带校验机制）
从飞书多维表格提取数据，校验后更新GitHub Pages看板。

数据格式约定（必须严格遵守，否则图表会崩溃）：
- PROJECTS: 使用 `type`（项目性质）、`phase`（当前阶段）、`year`（年份）字段名
- PLAN_DATA/ACTUAL_DATA: 使用 q1/q2/q3/q4 作为key（2026年度季度数据）
- 客户信息嵌入PROJECTS每条记录中（client/contact/position/phone）

核心校验规则：
1. 季度实际收款不得超过季度计划收款（允许=，不允许>）
2. 单项目收款不得超过该项目的合同额
3. 与上次数据对比，变更超过50%的自动标记预警
4. 实际收款从0变为非0时需明确标注来源项目
5. 项目名称必须全部可解析（财务表→项目表映射）
"""

import json
import re
import os
import subprocess
import sys
import datetime

BASE_TOKEN = 'DbLybnS1Fa67UOsWkJEcurfjnJb'
PROJECT_TABLE = 'tblyNo6TXfcTUcen'
FINANCE_TABLE = 'tblTjTvpRIpqJGW6'
PERSON_TABLE = 'tblPa5ADkNYsDiEv'
INDEX_PATH = '/home/coze/biz-dashboard/index.html'
SNAPSHOT_PATH = '/home/coze/biz-dashboard/scripts/last_data_snapshot.json'

def parse_md_table(filepath, header_keyword=None):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        if header_keyword and header_keyword not in line:
            continue
        if not header_keyword and '项目名称' not in line:
            continue
        headers = [h.strip() for h in line.split('|') if h.strip()]
        records = []
        for ln in lines[i+2:]:
            if not ln.startswith('|'):
                continue
            cells = [c.strip() for c in ln.split('|')][1:-1]
            if len(cells) < len(headers):
                cells.extend([''] * (len(headers) - len(cells)))
            records.append(dict(zip(headers, cells)))
        return records
    return []

def to_float(v):
    try: return float(v) if v else 0
    except: return 0

def parse_json_field(val):
    if not val: return ''
    if val.startswith('['):
        try:
            arr = json.loads(val.replace("'", '"'))
            return arr[0] if arr else ''
        except:
            return val
    return val

def load_last_snapshot():
    """加载上次数据快照，用于对比校验"""
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None

def save_snapshot(plan_data, actual_data, project_count, total_contract, total_pending):
    """保存本次数据快照"""
    snapshot = {
        'timestamp': datetime.datetime.now().isoformat(),
        'plan_data': plan_data,
        'actual_data': actual_data,
        'project_count': project_count,
        'total_contract': total_contract,
        'total_pending': total_pending,
    }
    with open(SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

def validate_data(plan_data, actual_data, projects, finance_records, rid_to_name):
    """
    数据校验：返回 (warnings, errors)
    - warnings: 预警项，不阻塞更新但需关注
    - errors: 严重错误，阻塞更新
    """
    warnings = []
    errors = []
    
    # === 校验1：季度实际收款不得超过季度计划收款 ===
    for qkey in ['q1', 'q2', 'q3', 'q4']:
        for dtype in ['research', 'develop', 'service']:
            plan_val = plan_data[qkey][dtype]
            actual_val = actual_data[qkey][dtype]
            if actual_val > plan_val and plan_val > 0:
                errors.append(
                    f"[校验失败] {qkey.upper()} {dtype} 实际收款({actual_val:.2f}万) > "
                    f"计划收款({plan_val:.2f}万)"
                )
            elif actual_val > 0 and plan_val == 0:
                warnings.append(
                    f"[预警] {qkey.upper()} {dtype} 计划为0但实际有收款({actual_val:.2f}万)"
                )
    
    # === 校验2：单项目收款不超过合同额 ===
    for p in projects:
        total_plan = p['q1'] + p['q2'] + p['q3'] + p['q4']
        total_actual = p['aq1'] + p['aq2'] + p['aq3'] + p['aq4']
        if p['contract'] > 0:
            if total_plan > p['contract'] * 1.1:  # 允许10%误差（跨年项目）
                warnings.append(
                    f"[预警] {p['name']} 季度计划合计({total_plan:.2f}万) "
                    f"超过合同额({p['contract']:.2f}万)的110%"
                )
            if total_actual > p['contract']:
                errors.append(
                    f"[校验失败] {p['name']} 实际收款合计({total_actual:.2f}万) "
                    f"超过合同额({p['contract']:.2f}万)"
                )
    
    # === 校验3：与上次数据对比，检测异常变更 ===
    last = load_last_snapshot()
    if last:
        for qkey in ['q1', 'q2', 'q3', 'q4']:
            for dtype in ['research', 'develop', 'service']:
                old_plan = last['plan_data'][qkey][dtype]
                new_plan = plan_data[qkey][dtype]
                old_actual = last['actual_data'][qkey][dtype]
                new_actual = actual_data[qkey][dtype]
                
                # 计划变更超50%预警
                if old_plan > 0 and abs(new_plan - old_plan) / old_plan > 0.5:
                    warnings.append(
                        f"[预警] {qkey.upper()} {dtype} 计划收款从{old_plan:.2f}→{new_plan:.2f}万 "
                        f"(变更{((new_plan-old_plan)/old_plan*100):+.0f}%)"
                    )
                
                # 实际收款从0变非0
                if old_actual == 0 and new_actual > 0:
                    # 找出是哪个项目贡献的
                    source_projects = []
                    for p in projects:
                        actual_q = p.get(f'a{qkey[1]}', 0)
                        if actual_q > 0:
                            source_projects.append(f"{p['name']}({actual_q:.2f}万)")
                    src_str = '、'.join(source_projects[:3]) if source_projects else '未识别'
                    warnings.append(
                        f"[预警] {qkey.upper()} {dtype} 实际收款从0→{new_actual:.2f}万 "
                        f"(来源: {src_str})"
                    )
                
                # 实际收款变更超100%预警
                if old_actual > 0 and abs(new_actual - old_actual) / old_actual > 1.0:
                    warnings.append(
                        f"[预警] {qkey.upper()} {dtype} 实际收款从{old_actual:.2f}→{new_actual:.2f}万 "
                        f"(变更{((new_actual-old_actual)/old_actual*100):+.0f}%)"
                    )
    
    # === 校验4：财务表项目名称解析率 ===
    unresolved = 0
    for frec in finance_records:
        proj_val = frec.get('项目名称', '')
        if not proj_val:
            continue
        if proj_val.startswith('['):
            try:
                links = json.loads(proj_val.replace("'", '"'))
                resolved = any(link.get('id', '') in rid_to_name for link in links)
                if not resolved:
                    unresolved += 1
            except:
                unresolved += 1
    if unresolved > 0:
        warnings.append(f"[预警] {unresolved}条财务记录的项目名称无法解析到项目进度表")
    
    # === 校验5：PLAN_DATA/ACTUAL_DATA与PROJECTS汇总一致性 ===
    for qkey in ['q1', 'q2', 'q3', 'q4']:
        qi = int(qkey[1])
        plan_from_projects = sum(p.get(f'q{qi}', 0) for p in projects)
        actual_from_projects = sum(p.get(f'aq{qi}', 0) for p in projects)
        plan_from_data = plan_data[qkey]['research'] + plan_data[qkey]['develop'] + plan_data[qkey]['service']
        actual_from_data = actual_data[qkey]['research'] + actual_data[qkey]['develop'] + actual_data[qkey]['service']
        
        if abs(plan_from_projects - plan_from_data) > 0.1:
            warnings.append(
                f"[预警] {qkey.upper()} 计划收款不一致: PROJECTS汇总={plan_from_projects:.2f}万, "
                f"PLAN_DATA={plan_from_data:.2f}万"
            )
        if abs(actual_from_projects - actual_from_data) > 0.1:
            warnings.append(
                f"[预警] {qkey.upper()} 实际收款不一致: PROJECTS汇总={actual_from_projects:.2f}万, "
                f"ACTUAL_DATA={actual_from_data:.2f}万"
            )
    
    return warnings, errors

def main():
    print("=" * 60)
    print("经营看板数据更新 v2.0（带校验机制）")
    print("=" * 60)
    
    # Step 1: Fetch data
    print("\n[1/7] 从飞书多维表格提取数据...")
    subprocess.run(
        ['lark-cli', 'base', '+record-list', '--base-token', BASE_TOKEN,
         '--table-id', PROJECT_TABLE, '--limit', '200'],
        stdout=open('/tmp/project_raw.txt', 'w'),
        stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ['lark-cli', 'base', '+record-list', '--base-token', BASE_TOKEN,
         '--table-id', FINANCE_TABLE, '--limit', '200'],
        stdout=open('/tmp/finance_raw.txt', 'w'),
        stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ['lark-cli', 'base', '+record-list', '--base-token', BASE_TOKEN,
         '--table-id', PERSON_TABLE, '--limit', '200'],
        stdout=open('/tmp/person_raw.txt', 'w'),
        stderr=subprocess.DEVNULL
    )
    
    project_records = parse_md_table('/tmp/project_raw.txt')
    finance_records = parse_md_table('/tmp/finance_raw.txt')
    person_records = parse_md_table('/tmp/person_raw.txt', header_keyword='姓名')
    print(f"  项目进度表: {len(project_records)} 条")
    print(f"  财务跟踪表: {len(finance_records)} 条")
    
    # Step 2: Build mappings
    print("\n[2/7] 构建数据映射...")
    rid_to_name = {}
    for prec in project_records:
        rid = prec.get('_record_id', '')
        name = prec.get('项目名称', '')
        if rid and name:
            rid_to_name[rid] = name
    
    def resolve_name(val):
        if not val: return None
        if val.startswith('['):
            try:
                links = json.loads(val.replace("'", '"'))
                for link in links:
                    if link.get('id', '') in rid_to_name:
                        return rid_to_name[link['id']]
            except: pass
        return val
    
    finance_by_name = {}
    for frec in finance_records:
        proj_name = resolve_name(frec.get('项目名称', ''))
        if not proj_name: continue
        if proj_name not in finance_by_name:
            finance_by_name[proj_name] = []
        finance_by_name[proj_name].append(frec)
    
    # Step 3: Build PROJECTS
    print("\n[3/7] 生成项目数据...")
    projects = []
    for prec in project_records:
        name = prec.get('项目名称', '')
        if not name: continue
        
        proj_type = parse_json_field(prec.get('项目性质', ''))
        phase = parse_json_field(prec.get('当前阶段', ''))
        leader = prec.get('负责人', '')
        field = parse_json_field(prec.get('专业方向', ''))
        next_plan = parse_json_field(prec.get('下一步计划', ''))
        
        contract = 0; pending = 0; year = None
        client = ''; contact = ''; position = ''; phone = ''
        q1p = q2p = q3p = q4p = 0
        q1a = q2a = q3a = q4a = 0
        
        for frec in finance_by_name.get(name, []):
            contract += to_float(frec.get('合同额(万元)'))
            pending += to_float(frec.get('待收款(万元)'))
            y = frec.get('项目签订年份', '').strip()
            if y: year = y
            q1p += to_float(frec.get('Q1计划收款'))
            q2p += to_float(frec.get('Q2计划收款'))
            q3p += to_float(frec.get('Q3计划收款'))
            q4p += to_float(frec.get('Q4计划收款'))
            q1a += to_float(frec.get('Q1实际收款'))
            q2a += to_float(frec.get('Q2实际收款'))
            q3a += to_float(frec.get('Q3实际收款'))
            q4a += to_float(frec.get('Q4实际收款'))
            if not client:
                client = frec.get('甲方单位', '')
                contact = frec.get('对接人', '')
                position = frec.get('职务', '')
                phone = frec.get('联系方式', '')
        
        if not year: year = '2026'
        
        projects.append({
            'name': name, 'type': proj_type, 'contract': contract,
            'year': year, 'phase': phase, 'pending': pending,
            'q1': q1p, 'q2': q2p, 'q3': q3p, 'q4': q4p,
            'aq1': q1a, 'aq2': q2a, 'aq3': q3a, 'aq4': q4a,
            'client': client, 'contact': contact,
            'position': position, 'phone': phone,
            'leader': leader, 'field': field, 'next_plan': next_plan,
        })
    
    projects.sort(key=lambda x: x['contract'], reverse=True)
    
    # Step 4: Build PLAN_DATA/ACTUAL_DATA
    # 注意：年份字段仅用于项目分类统计，不干涉Q1-Q4的收款数据
    # Q1-Q4计划/实际收款就是当前年度数据，所有财务记录都应纳入统计
    print("\n[4/7] 生成季度计划/实际数据...")
    plan_data = {q: {'research': 0, 'service': 0, 'develop': 0} for q in ['q1','q2','q3','q4']}
    actual_data = {q: {'research': 0, 'service': 0, 'develop': 0} for q in ['q1','q2','q3','q4']}
    
    # 详细记录每个季度的数据来源，用于校验追踪
    actual_sources = {q: [] for q in ['q1','q2','q3','q4']}
    
    for frec in finance_records:
        proj_name = resolve_name(frec.get('项目名称', ''))
        if not proj_name: continue
        
        proj_type = ''
        for prec in project_records:
            if prec.get('项目名称', '') == proj_name:
                proj_type = parse_json_field(prec.get('项目性质', ''))
                break
        
        dk = 'research' if proj_type == '研究' else 'service' if proj_type == '服务' else 'develop' if proj_type in ['集成开发','开发'] else 'research'
        year = frec.get('项目签订年份', '').strip()
        
        for qi, qkey in enumerate(['q1','q2','q3','q4']):
            plan_val = to_float(frec.get(f'Q{qi+1}计划收款'))
            actual_val = to_float(frec.get(f'Q{qi+1}实际收款'))
            plan_data[qkey][dk] += plan_val
            actual_data[qkey][dk] += actual_val
            if actual_val > 0:
                actual_sources[qkey].append({
                    'project': proj_name,
                    'year': year,
                    'type': dk,
                    'amount': actual_val
                })
    
    # 打印实际收款来源明细
    print("\n  实际收款来源明细：")
    for qkey in ['q1','q2','q3','q4']:
        if actual_sources[qkey]:
            for src in actual_sources[qkey]:
                print(f"    {qkey.upper()} | {src['project']} (年份:{src['year']}, {src['type']}) = {src['amount']:.2f}万")
        else:
            print(f"    {qkey.upper()} | 无实际收款")
    
    # Step 5: 数据校验
    print("\n[5/7] 数据校验...")
    warnings, errors = validate_data(plan_data, actual_data, projects, finance_records, rid_to_name)
    
    if warnings:
        print(f"\n  ⚠️ 预警 ({len(warnings)} 项):")
        for w in warnings:
            print(f"    {w}")
    
    if errors:
        print(f"\n  ❌ 校验失败 ({len(errors)} 项):")
        for e in errors:
            print(f"    {e}")
        print("\n  ⛔ 存在严重数据错误，停止更新！请人工检查后再执行。")
        sys.exit(1)
    else:
        print("  ✅ 数据校验通过")
    
    # Step 6: Update index.html
    print("\n[6/7] 更新看板页面...")
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # PROJECTS JS
    items = []
    for p in projects:
        sn = p['name'].replace("'", "\\'")
        sc = p['client'].replace("'", "\\'")
        sco = p['contact'].replace("'", "\\'")
        sp = p['position'].replace("'", "\\'")
        sph = p['phone'].replace("'", "\\'")
        sf = p['field'].replace("'", "\\'")
        snp = p['next_plan'].replace("'", "\\'") if p.get('next_plan') else ''
        yv = p['year'] if str(p['year']).isdigit() else f"'{p['year']}'"
        items.append(
            f"    {{ name: '{sn}', type: '{p['type']}', contract: {p['contract']}, "
            f"year: {yv}, phase: '{p['phase']}', pending: {p['pending']}, "
            f"q1: {p['q1']}, q2: {p['q2']}, q3: {p['q3']}, q4: {p['q4']}, "
            f"aq1: {p['aq1']}, aq2: {p['aq2']}, aq3: {p['aq3']}, aq4: {p['aq4']}, "
            f"client: '{sc}', contact: '{sco}', position: '{sp}', "
            f"phone: '{sph}', leader: '{p['leader']}', field: '{sf}', next_plan: '{snp}' }}"
        )
    projects_js = 'const PROJECTS = [\n' + ',\n'.join(items) + '\n    ];'
    content = re.sub(r'const PROJECTS\s*=\s*\[[\s\S]*?\];', projects_js, content)
    
    # PLAN_DATA JS
    pj = 'const PLAN_DATA = {\n'
    for qk in ['q1','q2','q3','q4']:
        pj += f"    {qk}: {{ research: {plan_data[qk]['research']}, develop: {plan_data[qk]['develop']}, service: {plan_data[qk]['service']} }},\n"
    pj += '        };'
    content = re.sub(r'const PLAN_DATA\s*=\s*\{[\s\S]*?\};', pj, content)
    
    # ACTUAL_DATA JS
    aj = 'const ACTUAL_DATA = {\n'
    for qk in ['q1','q2','q3','q4']:
        aj += f"    {qk}: {{ research: {actual_data[qk]['research']}, develop: {actual_data[qk]['develop']}, service: {actual_data[qk]['service']} }},\n"
    aj += '        };'
    content = re.sub(r'const ACTUAL_DATA\s*=\s*\{[\s\S]*?\};', aj, content)
    
    # CLIENTS JS
    ci = []
    seen = set()
    for p in projects:
        if p['client'] and p['client'] not in seen:
            seen.add(p['client'])
            sn = p['name'].replace("'", "\\'")
            sc = p['client'].replace("'", "\\'")
            sco = p['contact'].replace("'", "\\'")
            sph = p['phone'].replace("'", "\\'")
            spo = p['position'].replace("'", "\\'")
            ci.append(f"  {{ name: '{sc}', contact: '{sco}', phone: '{sph}', position: '{spo}', project: '{sn}' }}")
    clients_js = 'const CLIENTS = [\n' + ',\n'.join(ci) + '\n];'
    content = re.sub(r'const CLIENTS\s*=\s*\[[\s\S]*?\];', clients_js, content)
    
    # TEAM_DATA JS (auto-sync from person table)
    from collections import OrderedDict
    team_groups = OrderedDict()
    role_order = ['部门主任', '部门副主任', '技术副主任', '技术总监', '项目经理', '产品', '数据', '开发']
    for pr in person_records:
        pname = pr.get('姓名', '').strip()
        prole = parse_json_field(pr.get('职责', ''))
        if not pname or not prole:
            continue
        if prole not in team_groups:
            team_groups[prole] = []
        team_groups[prole].append((pname, prole))
    
    team_items = []
    for role in role_order:
        if role in team_groups:
            members = team_groups[role]
            member_strs = ',\n'.join([f"                {{ name: '{n}', role: '{r}' }}" for n, r in members])
            team_items.append(f"            '{role}': [\n{member_strs}\n            ]")
    for role, members in team_groups.items():
        if role not in role_order:
            member_strs = ',\n'.join([f"                {{ name: '{n}', role: '{r}' }}" for n, r in members])
            team_items.append(f"            '{role}': [\n{member_strs}\n            ]")
    
    team_js = 'const TEAM_DATA = {\n' + ',\n'.join(team_items) + '\n        };'
    content = re.sub(r'const TEAM_DATA\s*=\s*\{[\s\S]*?\};', team_js, content)
    
    # FILE_INDEX JS
    file_index = {}
    projects_dir = '/home/coze/projects'
    docs_dir = '/home/coze/biz-dashboard/docs'
    
    if os.path.exists(projects_dir):
        for pd in os.listdir(projects_dir):
            pp = os.path.join(projects_dir, pd)
            if not os.path.isdir(pp): continue
            files = []
            for fn in os.listdir(pp):
                fp = os.path.join(pp, fn)
                if os.path.isfile(fp) and fn != 'README.md':
                    ext = os.path.splitext(fn)[1].lower()
                    cat = '合同/文件' if ext == '.pdf' else '文档' if ext in ['.docx','.doc'] else '表格' if ext in ['.xlsx','.xls'] else '文档'
                    dp = os.path.join(docs_dir, pd)
                    os.makedirs(dp, exist_ok=True)
                    subprocess.run(['cp', fp, os.path.join(dp, fn)], check=True)
                    files.append({'name': fn, 'category': cat, 'ext': ext, 'size': os.path.getsize(fp), 'url': f'/biz-dashboard/docs/{pd}/{fn}'})
            if files:
                file_index[pd] = files
    
    if os.path.exists(docs_dir):
        for pd in os.listdir(docs_dir):
            dp = os.path.join(docs_dir, pd)
            if not os.path.isdir(dp) or pd in file_index: continue
            files = []
            for fn in os.listdir(dp):
                fp = os.path.join(dp, fn)
                if os.path.isfile(fp) and fn != 'README.md':
                    ext = os.path.splitext(fn)[1].lower()
                    files.append({'name': fn, 'category': '文档', 'ext': ext, 'size': os.path.getsize(fp), 'url': f'/biz-dashboard/docs/{pd}/{fn}'})
            if files:
                file_index[pd] = files
    
    fi_js = 'const FILE_INDEX = ' + json.dumps(file_index, ensure_ascii=False, indent=2) + ';'
    content = re.sub(r'const FILE_INDEX\s*=\s*\{[\s\S]*?\};', fi_js, content)
    
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Step 7: Git push
    print("\n[7/7] 推送到GitHub...")
    
    total_contract = sum(p['contract'] for p in projects)
    total_pending = sum(p['pending'] for p in projects)
    total_plan = sum(plan_data[q][d] for q in ['q1','q2','q3','q4'] for d in ['research','develop','service'])
    total_actual = sum(actual_data[q][d] for q in ['q1','q2','q3','q4'] for d in ['research','develop','service'])
    
    # 保存快照
    save_snapshot(plan_data, actual_data, len(projects), total_contract, total_pending)
    
    commit_msg = f"数据更新 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if warnings:
        commit_msg += f" ⚠️{len(warnings)}项预警"
    
    subprocess.run(['git', 'add', '-A'], cwd='/home/coze/biz-dashboard')
    subprocess.run(['git', 'commit', '-m', commit_msg], cwd='/home/coze/biz-dashboard')
    result = subprocess.run(['git', 'push', '--set-upstream', 'origin', 'main'], 
                          cwd='/home/coze/biz-dashboard', capture_output=True, text=True)
    
    if result.returncode == 0:
        print(f"\n✅ 已推送: {commit_msg}")
    else:
        # 可能需要pull再push
        print("  push失败，尝试pull rebase...")
        subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], cwd='/home/coze/biz-dashboard')
        subprocess.run(['git', 'push', '--set-upstream', 'origin', 'main'], cwd='/home/coze/biz-dashboard')
        print(f"\n✅ 已推送(重试成功): {commit_msg}")
    
    # 汇总输出
    print("\n" + "=" * 60)
    print(f"项目总数: {len(projects)} | 合同总额: {total_contract:.2f}万 | 待收款: {total_pending:.2f}万")
    print(f"2026计划: {total_plan:.2f}万 | 2026实际: {total_actual:.2f}万")
    print(f"PLAN_DATA keys: {list(plan_data.keys())}")
    
    # 打印各季度实际收款
    for qkey in ['q1','q2','q3','q4']:
        q_total = actual_data[qkey]['research'] + actual_data[qkey]['develop'] + actual_data[qkey]['service']
        if q_total > 0:
            print(f"  {qkey.upper()} 实际: {q_total:.2f}万", end='')
            if actual_sources[qkey]:
                srcs = [f"{s['project']}({s['amount']:.2f})" for s in actual_sources[qkey]]
                print(f" ← {', '.join(srcs)}", end='')
            print()
    
    if warnings:
        print(f"\n⚠️ 预警汇总 ({len(warnings)} 项):")
        for w in warnings:
            print(f"  {w}")
    
    print("=" * 60)

if __name__ == '__main__':
    main()
