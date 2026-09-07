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
                             'dashboard_record': {k: copy.deepcopy(paper[k]) for k in ('metadata', 'collections', 'annotation_count', 'reading_time_minutes', 'reading_by_day', 'read_dates') if k in paper},
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
    from library_dashboard import render
    return render(index, readonly)


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
        e.pop('dashboard_record', None)
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
