#!/usr/bin/env python3
"""
经营看板数据更新脚本（永久版本）
从飞书多维表格提取数据，更新GitHub Pages看板。

数据格式约定（必须严格遵守，否则图表会崩溃）：
- PROJECTS: 使用 `type`（项目性质）、`phase`（当前阶段）、`year`（年份）字段名
- PLAN_DATA/ACTUAL_DATA: 使用 q1/q2/q3/q4 作为key（2026年度季度数据）
- 客户信息嵌入PROJECTS每条记录中（client/contact/position/phone）
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
INDEX_PATH = '/home/coze/biz-dashboard/index.html'

def parse_md_table(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        if line.startswith('|') and '项目名称' in line:
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

def main():
    print("=" * 60)
    print("经营看板数据更新开始")
    print("=" * 60)
    
    # Step 1: Fetch data
    print("\n[1/6] 从飞书多维表格提取数据...")
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
    
    project_records = parse_md_table('/tmp/project_raw.txt')
    finance_records = parse_md_table('/tmp/finance_raw.txt')
    print(f"  项目进度表: {len(project_records)} 条")
    print(f"  财务跟踪表: {len(finance_records)} 条")
    
    # Step 2: Build mappings
    print("\n[2/6] 构建数据映射...")
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
    print("\n[3/6] 生成项目数据...")
    projects = []
    for prec in project_records:
        name = prec.get('项目名称', '')
        if not name: continue
        
        proj_type = parse_json_field(prec.get('项目性质', ''))
        phase = parse_json_field(prec.get('当前阶段', ''))
        leader = prec.get('负责人', '')
        field = parse_json_field(prec.get('专业方向', ''))
        
        contract = 0; pending = 0; year = None
        client = ''; contact = ''; position = ''; phone = ''
        q1p = q2p = q3p = q4p = 0
        q1a = q2a = q3a = q4a = 0
        
        for frec in finance_by_name.get(name, []):
            contract += to_float(frec.get('合同额(万元)'))
            pending += to_float(frec.get('待收款(万元)'))
            y = frec.get('年份', '').strip()
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
        
        # CRITICAL: Use `type` and `phase` to match chart code!
        projects.append({
            'name': name, 'type': proj_type, 'contract': contract,
            'year': year, 'phase': phase, 'pending': pending,
            'q1': q1p, 'q2': q2p, 'q3': q3p, 'q4': q4p,
            'aq1': q1a, 'aq2': q2a, 'aq3': q3a, 'aq4': q4a,
            'client': client, 'contact': contact,
            'position': position, 'phone': phone,
            'leader': leader, 'field': field,
        })
    
    projects.sort(key=lambda x: x['contract'], reverse=True)
    
    # Step 4: Build PLAN_DATA/ACTUAL_DATA (2026 quarterly)
    # 注意：年份字段仅用于项目分类统计，不干涉Q1-Q4的收款数据
    # Q1-Q4计划/实际收款就是2026年度数据，所有财务记录都应纳入统计
    print("\n[4/6] 生成季度计划/实际数据(2026)...")
    plan_data = {q: {'research': 0, 'service': 0, 'develop': 0} for q in ['q1','q2','q3','q4']}
    actual_data = {q: {'research': 0, 'service': 0, 'develop': 0} for q in ['q1','q2','q3','q4']}
    
    for frec in finance_records:
        proj_name = resolve_name(frec.get('项目名称', ''))
        if not proj_name: continue
        
        proj_type = ''
        for prec in project_records:
            if prec.get('项目名称', '') == proj_name:
                proj_type = parse_json_field(prec.get('项目性质', ''))
                break
        
        dk = 'research' if proj_type == '研究' else 'service' if proj_type == '服务' else 'develop' if proj_type in ['集成开发','开发'] else 'research'
        
        for qi, qkey in enumerate(['q1','q2','q3','q4']):
            plan_data[qkey][dk] += to_float(frec.get(f'Q{qi+1}计划收款'))
            actual_data[qkey][dk] += to_float(frec.get(f'Q{qi+1}实际收款'))
    
    # Step 5: FILE_INDEX
    print("\n[5/6] 更新文件索引...")
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
    
    # Step 6: Update index.html
    print("\n[6/6] 更新看板页面...")
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
        yv = p['year'] if str(p['year']).isdigit() else f"'{p['year']}'"
        items.append(
            f"    {{ name: '{sn}', type: '{p['type']}', contract: {p['contract']}, "
            f"year: {yv}, phase: '{p['phase']}', pending: {p['pending']}, "
            f"q1: {p['q1']}, q2: {p['q2']}, q3: {p['q3']}, q4: {p['q4']}, "
            f"aq1: {p['aq1']}, aq2: {p['aq2']}, aq3: {p['aq3']}, aq4: {p['aq4']}, "
            f"client: '{sc}', contact: '{sco}', position: '{sp}', "
            f"phone: '{sph}', leader: '{p['leader']}', field: '{sf}' }}"
        )
    projects_js = 'const PROJECTS = [\n' + ',\n'.join(items) + '\n    ];'
    content = re.sub(r'const PROJECTS\s*=\s*\[[\s\S]*?\];', projects_js, content)
    
    # PLAN_DATA JS (q1/q2/q3/q4 keys!)
    pj = 'const PLAN_DATA = {\n'
    for qk in ['q1','q2','q3','q4']:
        pj += f"    {qk}: {{ research: {plan_data[qk]['research']}, develop: {plan_data[qk]['develop']}, service: {plan_data[qk]['service']} }},\n"
    pj += '        };'
    content = re.sub(r'const PLAN_DATA\s*=\s*\{[\s\S]*?\};', pj, content)
    
    # ACTUAL_DATA JS (q1/q2/q3/q4 keys!)
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
    
    # FILE_INDEX JS
    fi_js = 'const FILE_INDEX = ' + json.dumps(file_index, ensure_ascii=False, indent=2) + ';'
    content = re.sub(r'const FILE_INDEX\s*=\s*\{[\s\S]*?\};', fi_js, content)
    
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Git push
    os.chdir('/home/coze/biz-dashboard')
    subprocess.run(['git', 'add', '-A'], check=True)
    r = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
    if r.stdout.strip():
        msg = f'数据更新 {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}'
        subprocess.run(['git', 'commit', '-m', msg], check=True)
        subprocess.run(['git', 'push', '--set-upstream', 'origin', 'main'], check=True)
        print(f"\n✅ 已推送: {msg}")
    else:
        print("\nℹ️ 无数据变更")
    
    # Summary
    tc = sum(p['contract'] for p in projects)
    tp = sum(p['pending'] for p in projects)
    ac = len([p for p in projects if p['phase'] not in ['撤销立项', '']])
    pt = sum(plan_data[q][t] for q in ['q1','q2','q3','q4'] for t in ['research','develop','service'])
    at = sum(actual_data[q][t] for q in ['q1','q2','q3','q4'] for t in ['research','develop','service'])
    
    print(f"\n{'='*60}")
    print(f"项目总数: {len(projects)} | 活跃: {ac}")
    print(f"合同总额: {tc:.2f}万 | 待收款: {tp:.2f}万")
    print(f"2026计划: {pt:.2f}万 | 2026实际: {at:.2f}万")
    print(f"PLAN_DATA keys: {list(plan_data.keys())}")
    print(f"PROJECTS字段: type={projects[0].get('type','')}, phase={projects[0].get('phase','')}")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()
