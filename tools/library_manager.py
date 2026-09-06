#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parent reading library: deterministic indexing, local editing, public backup.

stdlib only. Run `library_manager.py --root PATH update|serve|backup`.
"""
import argparse
import copy
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

LOCK = threading.RLock()
MARKER = '<!-- parent-library -->'
SKIP = {'work', 'skills', 'skill_rewrite_staging', 'public', '.git', 'library', 'tools', 'node_modules'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def load(path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding='utf-8'))


def digest(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') == data:
        return False
    tmp = path.with_name(path.name + '.tmp-' + secrets.token_hex(4))
    tmp.write_text(data, encoding='utf-8')
    tmp.replace(path)
    return True


def within(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('路径超出文献库')
    return p


def find_root(start):
    configured = os.environ.get('PAPER_READING_LIBRARY_ROOT')
    if configured:
        return Path(configured).expanduser().resolve()
    p = Path(start).resolve()
    for parent in [p, *p.parents]:
        if (parent / 'library/config.json').is_file():
            return parent
    return None


def attach(root, page, key):
    """Idempotently patch only navigation and the explicit-save integration."""
    source = page.read_text(encoding='utf-8')
    back = quote(os.path.relpath(root / 'dashboard.html', page.parent), safe='/')
    source = re.sub(r'href=[\"\'](?:\.\./)+dashboard\.html[\"\']', 'href="' + back + '"', source)
    save_line = 'try { localStorage.setItem(LS_KEY, JSON.stringify(edits)); } catch(e){}'
    if 'library-save' not in source:
        source = source.replace(save_line, save_line + '\n    window.dispatchEvent(new CustomEvent("library-save", {detail:edits}));')
    if 'library-load' not in source:
        source = source.replace('var storedEdits = null;', 'window.addEventListener("library-load", function(e){ applyEditsToDom(e.detail); if(e.detail.status) setStatus(e.detail.status,false); });\n  var storedEdits = null;')
    snippet = root / 'tools/library-client.js'
    if MARKER not in source and snippet.exists():
        client = quote(os.path.relpath(snippet, page.parent), safe='/')
        tag = MARKER + '<script src="' + client + '"></script>'
        source = source.replace('</body>', tag + '\n</body>')
    atomic(page, source)


def identity(meta, project, key):
    doi = str(meta.get('DOI', '')).lower().removeprefix('https://doi.org/').strip()
    if doi:
        return 'doi:' + doi
    arxiv = re.search(r'(\d{4}\.\d{4,5})(?:v\d+)?', str(meta.get('url', '')))
    if arxiv:
        return 'arxiv:' + arxiv[1]
    # Legacy local keys remain scoped by project. Title match is a candidate, not proof.
    return project + ':' + key


def update(root):
    root = root.resolve()
    with LOCK:
        old = load(root / 'library/index.json', {'entries': [], 'errors': []})
        entries = {e['id']: e for e in old['entries']}
        previous = copy.deepcopy(entries)
        errors, seen = [], set()
        projects = sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith('.'))
        for project in projects:
            notes = project / 'outputs/paper-notes'
            manifest = notes / 'reading-list.json'
            if not manifest.exists():
                continue
            try:
                papers = load(manifest)['papers']
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(project.name + ': 清单无法读取：' + str(exc))
                continue
            for paper in papers:
                eid = project.name + ':' + str(paper.get('zotero_key', ''))
                seen.add(eid)
                try:
                    key = str(paper['zotero_key'])
                    if not re.fullmatch(r'[\w-]+', key):
                        raise ValueError('无效论文 key')
                    page = within(root, str((notes / paper.get('html_path', 'papers/' + key + '.html')).relative_to(root)))
                    if not page.is_file():
                        raise ValueError('阅读页不存在：' + str(page.relative_to(root)))
                    editpath = notes / 'papers' / (key + '.edits.json')
                    edits = load(editpath, {})
                    render_files = [notes / 'papers' / (key + s) for s in ('.edits.json', '.summary.json', '.sections.json', '.quick-reading.json')]
                    inputs_hash = digest(''.join(f.read_text() for f in render_files if f.exists()))
                    prev_inputs = entries.get(eid, {}).get('render_inputs')
                    if (prev_inputs and inputs_hash != prev_inputs) or (not prev_inputs and edits):
                        skillbase = Path.home() / '.codex/skills'
                        env = dict(os.environ, LITERATURE_READER_OUTPUT_DIR=str(notes), PAPER_READING_LIBRARY_UPDATING='1')
                        md = project / 'outputs/reading/quick_reading.md'
                        if md.exists() and edits.get('modified_fields'):
                            bridge = skillbase / 'paper-reading/scripts/quick_reading_bridge.py'
                            proc = subprocess.run([sys.executable, str(bridge), 'sync', '--root', str(project), '--key', key, '--md', str(md), '--write-md'], env=env, capture_output=True, text=True)
                            if proc.returncode:
                                raise ValueError('编辑合并失败：' + proc.stderr[-700:])
                        renderer = skillbase / 'paper-notes/scripts/build_paper_html.py'
                        proc = subprocess.run([sys.executable, str(renderer), '--key', key], env=env, capture_output=True, text=True)
                        if proc.returncode:
                            raise ValueError('页面重建失败：' + proc.stderr[-700:])
                        inputs_hash = digest(''.join(f.read_text() for f in render_files if f.exists()))
                    attach(root, page, key)
                    meta = paper.get('metadata', {})
                    content = page.read_text(encoding='utf-8')
                    # Renderer timestamps alone do not represent a content update.
                    content = re.sub(r'GENERATED\s+[^<]+', 'GENERATED', content)
                    normalized = {k: v for k, v in paper.items() if k not in ('last_synced', 'generated_at')}
                    chunks = [content, json.dumps(normalized, sort_keys=True, ensure_ascii=False)]
                    for suffix in ('.summary.json', '.sections.json', '.quick-reading.json', '.edits.json'):
                        f = notes / 'papers' / (key + suffix)
                        if f.exists():
                            obj = load(f)
                            if isinstance(obj, dict):
                                obj = {k: v for k, v in obj.items() if k not in ('generated_at', 'last_synced')}
                            chunks.append(json.dumps(obj, sort_keys=True, ensure_ascii=False))
                    fingerprint = digest('\n'.join(chunks))
                    status = edits.get('status', paper.get('status', 'reading'))
                    entry = {'id': eid, 'paper_id': identity(meta, project.name, key), 'key': key,
                             'project': project.name, 'title': meta.get('title', key),
                             'year': str(meta.get('publicationYear', '')), 'venue': meta.get('venue', ''),
                             'status': status, 'html_path': page.relative_to(root).as_posix(),
                             'edits_path': editpath.relative_to(root).as_posix(),
                             'tags': paper.get('tags', []), 'fingerprint': fingerprint,
                             'render_inputs': inputs_hash,
                             'added_at': paper.get('date_added_to_reading') or entries.get(eid, {}).get('added_at'),
                             'updated_at': entries.get(eid, {}).get('updated_at', now()), 'available': True}
                    if fingerprint != entries.get(eid, {}).get('fingerprint'):
                        entry['updated_at'] = now()
                    entries[eid] = entry
                    alias = os.path.relpath(root / 'dashboard.html', notes)
                    atomic(notes / 'dashboard.html', '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=' + html.escape(alias) + '"><a href="' + html.escape(alias) + '">全部论文</a>')
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    errors.append(eid + ': ' + str(exc))
                    if eid in entries:
                        entries[eid]['available'] = False
        for eid in entries:
            if eid not in seen:
                entries[eid]['available'] = False
                errors.append(eid + ': 原项目或清单不可用，历史记录已保留')
        added = [e['title'] for k, e in entries.items() if k not in previous]
        changed = [e['title'] for k, e in entries.items() if k in previous and e != previous[k]]
        index = {'version': 1, 'entries': sorted(entries.values(), key=lambda e: e['id']), 'errors': errors}
        atomic(root / 'library/index.json', index)
        atomic(root / 'dashboard.html', dashboard(index))
        msg = ('更新完成：新增 %d 篇，更新 %d 篇' % (len(added), len(changed))) if added or changed else '暂无可更新内容'
        if errors:
            msg = '部分更新完成，%d 项需要处理' % len(errors)
        return {'ok': not errors, 'message': msg, 'added': added, 'changed': changed, 'errors': errors, 'index': index}


def dashboard(index, readonly=False):
    data = json.dumps(index, ensure_ascii=False).replace('<', '\\u003c')
    return r'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Reading · 全部论文</title><style>
:root{--rose:#b95d68;--tint:#fcf2f3;color:#423f3d;background:#fff;font-family:system-ui,-apple-system,sans-serif}body{max-width:1120px;margin:auto;padding:40px 24px}header{display:flex;justify-content:space-between;gap:24px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #eee;padding-bottom:24px}h1{font-size:36px;margin:8px 0}small{color:var(--rose);letter-spacing:2px}button,input,select{font:inherit;padding:10px 14px;border:1px solid #dfc9cc;border-radius:5px;background:white;color:inherit}button{cursor:pointer}button:hover{background:var(--tint)}button:disabled{opacity:.5}#notice{white-space:pre-wrap;min-height:28px;color:var(--rose)}#stats{padding:24px 0;font-size:18px}.filters{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:22px}input{flex:1;min-width:180px}#cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,310px),1fr));gap:18px}article{border:1px solid #eadcdf;padding:24px;border-top:3px solid #dc9aa3}a{color:inherit;text-decoration:none}a:hover{color:var(--rose)}h2{font-size:19px;line-height:1.55;margin:12px 0}p{line-height:1.7}.meta{color:#777;font-size:13px}.badge{background:var(--tint);color:var(--rose);padding:3px 8px;font-size:12px}footer{margin-top:40px;border-top:1px solid #eee;padding-top:20px;color:#888;font-size:13px}#history{margin:24px 0}.history-row{border-left:2px solid #e8bfc5;padding:5px 14px;font-size:14px}.hidden{display:none}</style>
<header><div><small>PERSONAL READING LIBRARY</small><h1>全部论文</h1><p>论文精读 · 方法理解 · 组会准备</p></div><div><button id="update">更新</button> <button id="backup">备份到 GitHub</button></div></header>
<div id="stats"></div><div class="filters"><input id="search" placeholder="搜索论文、项目或标签"><select id="status"><option value="">全部状态</option><option value="reading">阅读中</option><option value="done">已完成</option><option value="archived">已归档</option></select><select id="sort"><option value="recent">最近内容更新</option><option value="title">标题</option></select></div><p id="notice" role="status" aria-live="polite"></p><div id="cards"></div><details id="history"><summary>收录与内容更新记录</summary><div id="timeline"></div></details><footer>内容更新不等同于完成阅读。阅读状态来自已保存的记录。<span id="mode"></span></footer>
<script>let index=INDEX,readonly=READONLY;const $=s=>document.getElementById(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render(){let q=$('search').value.toLowerCase(),status=$('status').value;let all=index.entries;let unique=new Set(all.map(p=>p.paper_id));$('stats').textContent=`已收录 ${unique.size} 篇 · 阅读记录 ${all.length} 条 · 已完成 ${all.filter(p=>p.status==='done').length} 条`;let selected=all.filter(p=>(!status||p.status===status)&&[p.title,p.project,...(p.tags||[])].join(' ').toLowerCase().includes(q));selected.sort((a,b)=>$('sort').value==='title'?a.title.localeCompare(b.title):(b.updated_at||'').localeCompare(a.updated_at||''));let groups=new Map();selected.forEach(p=>{let g=groups.get(p.paper_id)||[];g.push(p);groups.set(p.paper_id,g)});$('cards').innerHTML=[...groups.values()].map(g=>{let p=g[0];return `<article><span class="badge">${esc({reading:'阅读中',done:'已完成',archived:'已归档'}[p.status]||p.status)}</span><h2>${esc(p.title)}</h2><p class="meta">${esc([p.year,p.venue].filter(Boolean).join(' · '))}</p>${g.map(e=>e.available?`<p><a href="${esc(e.html_path)}">打开阅读页 · ${esc(e.project)} ↗</a></p>`:`<p>路径不可用 · ${esc(e.project)}</p>`).join('')}<p class="meta">内容更新 ${esc(p.updated_at?.slice(0,10)||'未知')}</p></article>`}).join('')||'<p>没有匹配的论文。</p>';$('timeline').innerHTML=[...all].sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||'')).map(p=>`<p class="history-row">${esc(p.updated_at?.slice(0,10)||'日期未知')} · ${esc(p.title)} · 内容收录/更新</p>`).join('')}
['search','status','sort'].forEach(id=>$(id).addEventListener('input',render));async function action(name){if(readonly||location.protocol==='file:'){$('notice').textContent='更新服务未启动，请使用启动入口打开';return}let btn=$(name);btn.disabled=true;$('notice').textContent=name==='update'?'正在检查论文更新…':'正在备份…';try{let r=await fetch('/api/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});let data=await r.json();if(!r.ok)throw Error(data.message);$('notice').textContent=data.message+(data.errors?.length?'\n'+data.errors.join('\n'):'');if(data.index){index=data.index;render()}}catch(e){$('notice').textContent=e.message||'服务不可用'}finally{btn.disabled=false}}$('update').onclick=()=>action('update');$('backup').onclick=()=>action('backup');if(readonly){$('update').disabled=true;$('backup').disabled=true;$('mode').textContent=' · GitHub 公开阅读副本'}render();</script></html>'''.replace('INDEX', data).replace('READONLY', 'true' if readonly else 'false')


def run(args, cwd, timeout=90):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-1800:])
    return result.stdout.strip()


def export_public(root):
    """A deliberately public, readonly derivative; private originals never staged."""
    index = load(root / 'library/index.json')
    public = root / 'public'
    exported = []
    for entry in index['entries']:
        e = copy.deepcopy(entry)
        e.pop('edits_path', None)
        e.pop('fingerprint', None)
        e.pop('render_inputs', None)
        name = digest(e['id'])[:16] + '.html'
        md = root / entry['project'] / 'outputs/reading/quick_reading.md'
        if md.exists():
            prose = md.read_text(encoding='utf-8')
        else:
            summary = root / entry['project'] / 'outputs/paper-notes/papers' / (entry['key'] + '.summary.json')
            obj = load(summary, {})
            # Legacy public analysis only. Never export annotations/custom notes.
            chosen = {k: obj[k] for k in ('reading_guide', 'paper_reading', 'objective', 'approach', 'experiments_results', 'impact') if k in obj}
            prose = json.dumps(chosen, ensure_ascii=False, indent=2)
        prose = re.sub(r'/Users/[^\s\"<>`，；。]+', '[本机路径已省略]', prose)
        prose = re.sub(r'(?:/private)?/var/folders/[^\s\"<>`，；。]+', '[临时路径已省略]', prose)
        body = '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(e['title']) + '</title><style>body{max-width:960px;margin:40px auto;padding:24px;color:#423f3d;font:16px/1.8 system-ui}a{color:#b95d68}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}</style><a href="../dashboard.html">← 全部论文</a><h1>' + html.escape(e['title']) + '</h1><p>公开阅读副本 · 完整可编辑版本保存在本地文献库</p><pre>' + html.escape(prose) + '</pre></html>'
        atomic(public / 'papers' / name, body)
        e['html_path'] = 'papers/' + name
        e['available'] = True
        exported.append(e)
    atomic(public / 'dashboard.html', dashboard({'entries': exported, 'errors': []}, True))


def backup(root):
    with LOCK:
        cfg = load(root / 'library/config.json', {})
        remote = cfg.get('github_remote')
        if not remote:
            raise ValueError('尚未配置 GitHub 仓库')
        result = update(root)
        if result['errors']:
            raise ValueError('请先处理总览更新错误，再备份')
        export_public(root)
        if not (root / '.git').exists():
            run(['git', 'init', '-b', 'main'], root)
        remotes = run(['git', 'remote'], root).splitlines()
        if 'origin' not in remotes:
            run(['git', 'remote', 'add', 'origin', remote], root)
        elif run(['git', 'remote', 'get-url', 'origin'], root) != remote:
            raise ValueError('origin 与配置不一致，已停止推送')
        staged = run(['git', 'diff', '--cached', '--name-only'], root).splitlines()
        allowed = lambda p: p in ('README.md', '.gitignore', 'Start Reading.command') or p.startswith(('public/', 'tools/'))
        if any(not allowed(p) for p in staged):
            raise ValueError('暂存区含备份范围之外的文件，请先处理')
        run(['git', 'add', '--', 'README.md', '.gitignore', 'public', 'tools', 'Start Reading.command'], root)
        if run(['git', 'diff', '--cached', '--name-only'], root):
            run(['git', 'commit', '-m', 'Update paper reading library'], root)
        run(['git', 'fetch', 'origin'], root)
        # Push is fast-forward only; a divergence remains a reviewable local commit.
        output = run(['git', 'push', '-u', 'origin', 'main'], root)
        return {'ok': True, 'message': 'GitHub 备份已同步', 'commit': run(['git', 'rev-parse', '--short', 'HEAD'], root)}


def serve(root, port, open_browser):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') == '127.0.0.1:%d' % self.server.server_port

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {'message': '无效访问来源'})
            if self.path.startswith('/api/edits?'):
                try:
                    from urllib.parse import parse_qs
                    rel = parse_qs(urlparse(self.path).query)['page'][0]
                    e = next(e for e in load(root / 'library/index.json')['entries'] if e['html_path'] == rel)
                    p = within(root, e['edits_path'])
                    raw = p.read_text() if p.exists() else ''
                    return self.reply(200, {'revision': digest(raw), 'edits': json.loads(raw) if raw else {}})
                except Exception as exc:
                    return self.reply(400, {'message': str(exc)})
            path = unquote(urlparse(self.path).path).lstrip('/') or 'dashboard.html'
            try:
                p = within(root, path)
                if any(part.startswith('.') or part in SKIP - {'public', 'tools', 'library'} for part in Path(path).parts):
                    raise ValueError('此路径不提供网页访问')
                if not p.is_file():
                    raise ValueError('文件不存在')
            except ValueError as exc:
                return self.reply(404, {'message': str(exc)})
            self.path = '/' + quote(path, safe='/')
            return super().do_GET()

        def do_POST(self):
            expected = 'http://127.0.0.1:%d' % self.server.server_port
            if not self.valid_host() or self.headers.get('Origin') != expected or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(403, {'message': '只接受本机页面操作'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size < 8_000_000:
                    raise ValueError('请求长度无效')
                payload = json.loads(self.rfile.read(size))
                with LOCK:
                    if self.path == '/api/update':
                        return self.reply(200, update(root))
                    if self.path == '/api/backup':
                        return self.reply(200, backup(root))
                    if self.path != '/api/edits':
                        return self.reply(404, {'message': '未知操作'})
                    e = next(e for e in load(root / 'library/index.json')['entries'] if e['html_path'] == payload['page'])
                    p = within(root, e['edits_path'])
                    raw = p.read_text() if p.exists() else ''
                    if payload.get('revision') != digest(raw):
                        return self.reply(409, {'message': '另一页面已保存新版本，请导出当前编辑后重新载入'})
                    edits = payload['edits']
                    if not isinstance(edits, dict):
                        raise ValueError('编辑格式无效')
                    old = json.loads(raw) if raw else {}
                    if old.get('module_notes') and not edits.get('module_notes'):
                        edits['module_notes'] = old['module_notes']
                    atomic(p, edits)
                    return self.reply(200, {'ok': True, 'revision': digest(p.read_text())})
            except Exception as exc:
                self.reply(400, {'message': str(exc)})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = 'http://127.0.0.1:%d/dashboard.html' % server.server_port
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path)
    ap.add_argument('command', choices=['update', 'serve', 'backup', 'export'])
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--open', action='store_true')
    args = ap.parse_args()
    root = args.root.resolve() if args.root else find_root(Path.cwd())
    if root is None:
        ap.error('找不到文献库，请指定 --root 或建立 library/config.json')
    try:
        if args.command == 'serve':
            update(root)
            serve(root, args.port, args.open)
        elif args.command == 'export':
            export_public(root)
        else:
            result = update(root) if args.command == 'update' else backup(root)
            result.pop('index', None)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['ok'] else 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
