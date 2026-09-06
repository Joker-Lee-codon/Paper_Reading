/* Local-only persistence for explicitly edited reader fields. */
(() => {
  if (location.hostname !== '127.0.0.1' || location.protocol !== 'http:') return;
  const page = decodeURIComponent(location.pathname).replace(/^\//, '');
  let revision, pending, running = false, ready = false, conflicted = false;
  const status = document.createElement('div');
  status.style.cssText = 'position:fixed;bottom:12px;right:16px;max-width:440px;padding:9px 14px;background:#fcf2f3;color:#a65360;border:1px solid #ead0d5;z-index:9999;font:13px/1.5 system-ui';
  status.textContent = '正在连接本地保存…'; document.body.append(status);
  async function flush() {
    if (!ready || running || conflicted || !pending) return;
    running = true;
    const edits = pending; pending = null;
    try {
      const r = await fetch('/api/edits', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page,revision,edits})});
      const data = await r.json();
      if (!r.ok) { if(r.status===409) conflicted=true; throw Error(data.message); }
      revision = data.revision; status.textContent = '已保存到本地 · 可返回总览点击更新';
    } catch(e) { pending = pending || edits; status.textContent = '保存未完成：' + e.message + '（可 Export JSON 保留编辑）'; }
    finally { running=false; if(pending && !conflicted && status.textContent.startsWith('已保存')) flush(); }
  }
  window.addEventListener('library-save', e => { pending=e.detail; flush(); });
  fetch('/api/edits?page='+encodeURIComponent(page)).then(r=>{if(!r.ok)throw Error('当前页面未登记');return r.json()}).then(data=>{
    revision=data.revision;
    if(Object.keys(data.edits).length && !pending) window.dispatchEvent(new CustomEvent('library-load',{detail:data.edits}));
    ready=true; status.textContent='本地自动保存已连接'; flush();
  }).catch(e=>{status.textContent='本地保存不可用：'+e.message+' · 请使用 Sync Folder / Export JSON';});
  window.addEventListener('beforeunload', e=>{if(pending||running){e.preventDefault();e.returnValue='';}});
})();
