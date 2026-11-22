/* Native input receipts are not proof of model execution. Never replay them. */
(function (PR, W) {
  "use strict";
  const Q = W.sessionQueue = {};
  let signature = '';
  const active = new Set(['submitting','accepted','queued','unknown']);
  const label = status => ({submitting:PR.t('等待原生确认'),accepted:PR.t('已接受，等待投递'),queued:PR.t('原生队列中'),
    unknown:PR.t('结果待核对'),failed:PR.t('未成功提交'),cleared:PR.t('已从队列移除'),handled:PR.t('命令已处理'),delivered:PR.t('已投递')})[status] || status;
  Q.statusLabel = label;
  Q.live = session => (session?.pending_inputs || []).filter(item=>active.has(item.status)&&!item.retired);
  Q.render = session => {
    const box=W.$('ws-pending-inputs'), list=W.$('ws-pending-inputs-list');
    const items=(session?.pending_inputs || []).filter(item=>active.has(item.status)||item.status==='failed');
    const native=session?.native_queue || {}, total=Number(native.steering||0)+Number(native.follow_up||0);
    const next=JSON.stringify([session?.id,items,native,W.sessions.isPending(session?.id)]);
    if(next===signature)return;signature=next;
    box.hidden=!items.length&&!total;
    if(box.hidden){list.replaceChildren();return;}
    const waiting=Math.max(total,items.filter(item=>['submitting','accepted','queued'].includes(item.status)&&!item.retired).length);
    const uncertain=items.filter(item=>item.status==='unknown'||item.status==='failed').length;
    W.$('ws-pending-inputs-summary').textContent=PR.t('待发 {n} · 待核对 {m}',{n:waiting,m:uncertain});
    list.replaceChildren();
    for(const item of items) {
      const row=PR.el('div',{class:'ws-queued-input'});
      const mode=item.kind==='steer'?PR.t('引导'):item.kind==='follow_up'?PR.t('完成后继续'):PR.t('输入');
      row.append(PR.el('strong',{text:mode+' · '+label(item.status)+(item.retired?' · '+PR.t('已停止，未自动重试'):'')}),
        PR.el('p',{text:item.message||''}));
      if(item.images?.length)row.append(PR.el('small',{text:PR.t('附图 {n} 张',{n:item.images.length})}));
      if(item.error)row.append(PR.el('p',{class:'err',text:item.error}));
      row.append(W.button(PR.t('复制输入'),()=>PR.copyText(item.message||''),'copy','line'));
      list.append(row);
    }
    if(total) {
      const clear=W.button(PR.t('清空原生待发队列'),async()=>{
        const id=session.id;
        if(W.sessions.current?.id!==id||!await PR.confirm({title:PR.t('清空待发队列？'),body:PR.t('只移除尚未投递的输入，不停止当前执行；记录保留，不会自动重发。')}))return;
        if(W.sessions.current?.id===id)await W.sessions.mutate(id,'queue/clear',{});
      },'x','line');
      clear.disabled=!W.sessions.canControl?.(session.id,'queue/clear');list.append(clear);
    }
  };
})(window.PR, window.PR.workspaces);
