"""Adapt parent records to the existing Paper Notes dashboard, without restyling."""
import importlib.util
import json
import os
import sys
from pathlib import Path


def render(index, readonly=False):
    skill = Path(os.environ.get('PAPER_NOTES_SKILL_DIR', Path.home()/'.codex/skills/paper-notes'))
    scripts = skill/'scripts'
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location('parent_dashboard_builder', scripts/'build_dashboard.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
    finally:
        sys.path.pop(0)
    papers = []
    for e in index['entries']:
        record = dict(e.get('dashboard_record', {}))
        record.update(zotero_key=e['id'], status=e['status'], html_path=e['html_path'],
                      tags=e.get('tags', []))
        record['metadata'] = dict(record.get('metadata', {}), title=e['title'],
                                  publicationYear=e.get('year', ''), venue=e.get('venue', ''))
        papers.append(record)
    page = builder.build(include_archived=True, manifest={'papers': papers},
                         config={'default_accent':'rose', 'connect_zotero':not readonly},
                         generated_at=max((e.get('updated_at') or '' for e in index['entries']), default=''))
    # Keep the existing typography, theme, calendar, tree and controls unchanged.
    button = '<button class="tb-link" id="libraryUpdate" type="button" style="font:inherit;background:none;border:0;cursor:pointer;padding:0;white-space:nowrap;flex-shrink:0">更新</button>\n    '
    page = page.replace('<a class="tb-link"', button+'<a class="tb-link"', 1)
    page = page.replace('<div class="head-meta" id="statsRow"></div>', '<div class="head-meta" id="statsRow"></div><div id="libraryNotice" role="status" aria-live="polite" style="white-space:pre-wrap;font-size:12px;color:var(--accent-deep)" hidden></div>')
    script = r'''<script>
(() => {
 const button=document.getElementById('libraryUpdate'), notice=document.getElementById('libraryNotice');
 const show=text=>{notice.hidden=false;notice.textContent=text;};
 try {const previous=sessionStorage.getItem('library:update-result');if(previous){show(previous);sessionStorage.removeItem('library:update-result');}}catch(e){}
 if(READONLY){button.disabled=true;button.title='公开副本仅供阅读，请在本机文献库更新';return;}
 button.onclick=async()=>{
   if(location.protocol!=='http:'||location.hostname!=='127.0.0.1'){show('请先双击 PaperReading 文件夹中的 Start Reading.command，再在打开的总览中更新。');return;}
   button.disabled=true;show('正在检查更新…');
   try{const response=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
     const data=await response.json();if(!response.ok)throw Error(data.message||'更新失败');
     const message=data.message+(data.errors?.length?'\n'+data.errors.join('\n'):'');
     show(message);
     if(data.added?.length||data.changed?.length){try{sessionStorage.setItem('library:update-result',message);}catch(e){}location.reload();}
   }catch(e){show('更新未完成：'+e.message);}finally{button.disabled=false;}
 };
})();</script>'''.replace('READONLY', 'true' if readonly else 'false')
    return page.replace('</body>', script+'\n</body>')
