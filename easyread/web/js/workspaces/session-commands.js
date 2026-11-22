/* Native inventory, not guessed CLI commands. GUI actions reuse existing controls. */
(function (PR, W) {
  "use strict";
  const C = W.sessionCommands = {};
  const cache = new Map(), pending = new Map();
  let revision = 0, choices = [], selected = 0, range = null;
  const scope = () => [W.libraryId, W.project?.id, W.sessions.current?.id, W.sessions.current?.engine].join(':');
  const engine = () => W.sessions.current?.engine || W.project.engine;
  const can = name => W.sessions.supports?.(name) === true;
  const builtin = () => [
    {name:'help',description:PR.t('查看命令与 Skills')},
    {name:'new',description:PR.t('新建或恢复另一个会话')},
    {name:'model',description:PR.t('模型与思考强度')},
    {name:'settings',description:PR.t('打开当前会话设置')},
    {name:'name',description:PR.t('重命名会话')},
    {name:'stop',description:PR.t('停止当前执行，不重发待处理输入')},
    ...(can('compact') ? [{name:'compact',description:PR.t('使用原生上下文压缩')}] : []),
    ...(can('goal') && engine()==='codex' ? [{name:'goal',description:PR.t('设置原生目标')}] : [])
  ].map(item=>({...item,source:'gui'}));
  const localNames = new Set(['help','new','model','settings','name','stop','compact']);
  const tuiOnly = new Set(['login','logout','theme','hotkeys','keybindings','tree','fork','resume','export','quit','exit','reload','clear']);
  const parse = text => /^\s*\/([\p{L}\p{N}_.:-]+)(?:\s+([\s\S]*))?$/u.exec(text);
  const terminalOwned = () => !!W.sessions.current?.terminal_open || W.sessionTerminal.owns(W.sessions.current?.id);
  C.isLocal = text => { const m=parse(text);return !!m && localNames.has(m[1]); };
  C.close = () => {
    ++revision; range=null;choices=[];W.$('ws-commands').hidden=true;
    const input=W.$('ws-message');
    if (input.getAttribute?.('aria-controls')==='ws-commands') {
      input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-activedescendant'); input.setAttribute('aria-controls','ws-mentions');
    }
  };
  async function native(force=false) {
    const s=W.sessions.current,key=scope(),generation=s?.generation;
    if (!s || !can('commands') || terminalOwned()) return [];
    const saved=cache.get(key);
    if (!force && saved && saved.generation===generation && Date.now()-saved.at<60000) return saved.items;
    if (pending.has(key)) return pending.get(key);
    const request=W.api('/sessions/'+encodeURIComponent(s.id)+'/commands',{}).then(data=>{
      if (!Array.isArray(data.commands)) throw new Error(PR.t('原生命令目录不可用，请重试。'));
      const items=data.commands.slice(0,1000).filter(item=>item && typeof item.name==='string' && item.name.length<=160 && !/[\s\x00-\x1f]/.test(item.name)).map(item=>({
        name:item.name,description:String(item.description||'').slice(0,500),source:String(item.source||'extension')
      }));
      if(scope()===key) {
        if(generation && generation!==W.sessions.current?.generation)throw new Error(PR.t('原生运行实例已变化，请刷新命令目录。'));
        cache.set(key,{at:Date.now(),items,generation:W.sessions.current?.generation});while(cache.size>16)cache.delete(cache.keys().next().value);
      }
      return items;
    }).finally(()=>pending.delete(key));
    pending.set(key,request);return request;
  }
  C.inventory = async (force=false) => {
    const result=new Map(builtin().map(item=>[item.name,item]));
    for(const item of await native(force)) if(!result.has(item.name)) result.set(item.name,item);
    return [...result.values()];
  };
  const invocation = item => item.source==='skill' && engine()==='codex' ? '$'+item.name.replace(/^skill:/,'') : '/'+item.name;
  function insert(item, full=false) {
    const input=W.$('ws-message');
    if(!full && (!range || input.selectionStart!==range.caret || input.selectionEnd!==range.caret)) {C.close();return;}
    const text=invocation(item)+' ';
    // Explicit palette selection prepends without deleting an existing draft.
    if(full) input.setRangeText(text,0,0,'end');
    else input.setRangeText(text,range.from,range.to,'end');
    C.close();input.focus();W.sessions.persist();W.sessions.updateSend();
  }
  function render(items, message='') {
    const box=W.$('ws-commands');box.replaceChildren();box.hidden=false;
    const input=W.$('ws-message');input.setAttribute('aria-controls','ws-commands');input.setAttribute('aria-expanded','true');
    if(message) box.append(PR.el('p',{class:'ws-mention-empty',text:message}));
    items.forEach((item,index)=>{
      const button=PR.el('button',{type:'button',role:'option',id:'ws-command-'+index,'aria-selected':index===selected});
      button.tabIndex=-1;button.append(PR.el('span',{text:invocation(item)}),PR.el('small',{text:item.description||item.source}));
      button.onmousedown=e=>e.preventDefault();button.onclick=()=>insert(item);box.append(button);
    });
    if(items.length) input.setAttribute('aria-activedescendant','ws-command-'+selected);
    else input.removeAttribute('aria-activedescendant');
  }
  C.update = async () => {
    const input=W.$('ws-message'),before=input.value.slice(0,input.selectionStart);
    const match=/^\s*([/$])([^\s]*)$/.exec(before);
    if(!match || input.selectionStart!==input.selectionEnd || terminalOwned()) {C.close();return false;}
    W.mentions.close();
    const ticket=++revision,key=scope(),value=input.value,caret=input.selectionStart;
    let end=caret;while(end<value.length&&!/\s/.test(value[end]))end++;
    range={from:before.search(/[/$]/),to:end,caret};selected=0;
    render([],PR.t('正在读取命令…'));
    try {
      const all=await C.inventory();
      if(ticket!==revision||key!==scope()||input.value!==value||input.selectionStart!==caret)return true;
      const query=match[2].toLocaleLowerCase();
      choices=all.filter(item=>(match[1]!=='$'||item.source==='skill') && item.name.replace(/^skill:/,'').toLocaleLowerCase().includes(query)).slice(0,12);
      render(choices,choices.length?'':PR.t('没有匹配的命令或 Skill'));
    } catch(error) {if(ticket===revision)render([],error.message);}
    return true;
  };
  C.keydown = event => {
    if(!range||W.$('ws-commands').hidden)return false;
    if(event.key==='Escape'){event.preventDefault();C.close();return true;}
    if(['ArrowDown','ArrowUp'].includes(event.key)) {
      event.preventDefault();selected=(selected+(event.key==='ArrowDown'?1:-1)+choices.length)%(choices.length||1);render(choices);return true;
    }
    if(['Enter','Tab'].includes(event.key)&&!event.shiftKey&&!event.ctrlKey&&!event.metaKey&&!event.altKey) {
      const choice=choices[selected];
      if(event.key==='Enter' && (!choice || W.$('ws-message').value.trim()===invocation(choice))) { C.close(); return false; }
      event.preventDefault();if(choice)insert(choice);return true;
    }
    return false;
  };
  C.open = async () => {
    C.close();const key=scope(),dialog=W.modal(PR.t('命令与 Skills')),list=PR.el('div',{class:'ws-command-catalog'}),status=PR.el('p',{class:'hint',role:'status'});
    let loading=false;
    const renderMenu = commands => {
      list.replaceChildren();
      for(const item of commands) list.append(W.button(invocation(item)+' — '+item.description,()=>{
        if(key!==scope())return;
        dialog.close();insert(item,true);
      },null,'line'));
    };
    renderMenu(builtin());
    const load=async(force=false)=>{
      if(loading)return;loading=true;status.textContent=PR.t('正在读取命令…');
      try {
        const commands=await C.inventory(force);if(!dialog.isConnected||key!==scope())return;
        renderMenu(commands);
        status.textContent=can('commands')?PR.t('目录来自当前会话的原生 CLI；选择后可补充参数。'):PR.t('当前引擎未提供原生命令目录；这里显示 GUI 快捷操作。');
      } catch(error){status.textContent=error.message;} finally{loading=false;}
    };
    dialog.append(status,W.button(PR.t('刷新原生命令'),()=>load(true),'sync','line'),list);
    dialog.showModal();await load();
  };
  C.resolve = async typed => {
    const key=scope(),m=parse(typed);
    const skill=/^\s*\$([^\s$]+)(?:\s+([\s\S]*))?$/.exec(typed);
    if(!m&&!skill)return {message:typed,nativeCommand:false};
    if(m && (localNames.has(m[1]) || (m[1]==='goal'&&engine()==='codex')))return {message:typed,nativeCommand:false};
    const commands=await native();if(key!==scope())throw new Error(PR.t('会话已切换，请重新确认输入。'));
    if(skill) {
      const item=commands.find(item=>item.source==='skill'&&item.name.replace(/^skill:/,'')===skill[1]);
      if(!item)throw new Error(PR.t('未找到该原生 Skill，请刷新命令目录。'));
      // Keep the user's original spelling in the journal; the native adapter
      // performs its verified $skill → /skill:name expansion.
      return {message:typed,nativeCommand:false};
    }
    const found=commands.find(item=>item.name===m[1]);
    if(found)return {message:typed,nativeCommand:found.source==='extension'};
    if(tuiOnly.has(m[1]))throw new Error(PR.t('此命令需要在原生终端中执行，不会作为问题发送给模型。'));
    throw new Error(PR.t('当前原生 CLI 未提供该命令，请查看命令目录。'));
  };
  C.executeLocal = async typed => {
    const m=parse(typed);if(!m||!localNames.has(m[1]))return false;
    const S=W.sessions, id=S.current?.id, args=(m[2]||'').trim();
    if(W.sessionImages.any||W.mentions.files.length)throw new Error(PR.t('GUI 快捷命令不接收附件，请先移除附件。'));
    if(terminalOwned())throw new Error(PR.t('请先停止并关闭原生终端。'));
    if(args && ['help','stop'].includes(m[1]))throw new Error(PR.t('此 GUI 命令不接收参数，输入已保留。'));
    if(m[1]==='help')await C.open();
    else if(m[1]==='new') { if(args)throw new Error(PR.t('/new 不接收参数，请在新会话中输入任务。'));await S.create(); }
    else if(m[1]==='model'||m[1]==='settings') {
      if(args||!W.sessionSettings.available())throw new Error(PR.t('请在空闲会话的设置中选择模型。'));
      await W.sessionSettings.open();
    } else if(m[1]==='name') {
      if(!id||!args)throw new Error(PR.t('用法：/name 会话名称'));
      await S.mutate(id,'rename',{title:args});await S.refresh();
    } else if(m[1]==='stop') {
      if(!id)throw new Error(PR.t('请先选择会话。'));
      await S.mutate(id,'cancel',{});
    } else if(m[1]==='compact') {
      if(!id||!can('compact')||W.sessionOwnsWork(S.current))throw new Error(PR.t('当前会话不支持压缩或仍在执行任务。'));
      await S.mutate(id,'command',{command:'compact',arguments:args});
    }
    const input=W.$('ws-message');if(input.value.trim()===typed.trim()){input.value='';S.persist();S.updateSend();}
    return true;
  };
  C.init = () => {
    W.$('ws-command-menu').onclick=W.run(C.open);
    W.$('ws-message').addEventListener('blur',()=>setTimeout(()=>{if(!W.$('ws-commands').contains(document.activeElement))C.close();},150));
  };
})(window.PR, window.PR.workspaces);
