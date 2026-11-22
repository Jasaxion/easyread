const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const scripts = path.resolve(__dirname, '../../easyread/web/js/workspaces');
const flush = () => new Promise(resolve => setImmediate(resolve));
const snapshot = (id, warning) => ({session:{id,native_id:'same-native-'+id,engine:'pi',state:'idle',messages:[{id:'saved',role:'assistant',content:'Saved answer'}],events:[],pending_requests:[],...(warning?{native_sync_warning:warning}:{})},cursor:1,has_more:false});
function fixture() {
  const nodes = new Map(), drafts = new Map(), calls = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'',children:[],disabled:false,hidden:false,scrollTop:0,scrollHeight:0,
      set textContent(value) { this.text=value; this.children=[]; }, get textContent() { return this.text||''; },
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children=items; },
      setAttribute() {},querySelector:()=>({disabled:false})});
    return nodes.get(id);
  };
  node('ws-session-mode').value='chat';
  const W = {$:node,key:k=>k,store:(k,v)=>{drafts.set(k,JSON.parse(JSON.stringify(v)));return true;},run:fn=>fn,
    project:{id:'project',engine:'pi'},capabilities:{},sessionLabel:s=>s.title||s.id,goalStateLabel:s=>s||'',
    button:(text,onclick)=>({text,onclick}),renderConversation() {},renderSessionRequests() {},
    sessionTerminal:{owns:()=>false,init(){},render(){},observe(){},reconcile(){},dispose(){}},
    sessionImages:{restore(){},payload:()=>[],any:false,pending:false},
    mentions:{files:[],init(){},restore(value){this.files=value?.files||[];},close(){}},
    api:async(route,body)=>{calls.push({route,body});return W.respond(route,body);}
  };
  const PR={workspaces:W,t:s=>s,el:(tag,props)=>({...props}),ls:{get:(k,f)=>drafts.get(k)??f}};
  const context=vm.createContext({window:{PR},document:{createTextNode:text=>({text})},URLSearchParams});
  for(const name of ['session-capabilities.js','session-history.js','sessions.js']) vm.runInContext(fs.readFileSync(path.join(scripts,name),'utf8'),context);
  return {W,S:W.sessions,nodes,node,drafts,calls};
}

test('failed session initialization stays local, preserves drafts, and explicitly retries reads only', async()=>{
  const {W,S,node,drafts,calls}=fixture();
  drafts.set('message:new',{message:'Unsent draft',files:[],mode:'chat'});
  W.respond=()=>{throw Object.assign(new Error('Invalid Pi message'),{code:'invalid_transcript',status:409});};
  await S.init();
  assert.equal(S.loadError.message,'Invalid Pi message'); assert.equal(node('ws-message').value,'Unsent draft');
  assert.match(node('ws-session-notice').textContent,/项目文件仍可使用/);
  assert.equal(node('ws-session-notice').children.at(-1).text,'重新读取会话');
  assert.equal(node('ws-send').disabled,true);
  W.respond=route=>route==='/sessions'?{sessions:[snapshot('good').session]}:snapshot('good');
  await S.retry();
  assert.equal(S.current.id,'good'); assert.equal(S.loadError,null);
  assert.equal(drafts.get('message:new').message,'Unsent draft');
  node('ws-message').value='Keep current draft'; S.persist();
  const oldHistory=S.history;
  W.respond=()=>{throw new Error('Transient session read failure');};
  await S.poll();
  assert.equal(S.history,oldHistory); assert.equal(node('ws-message').value,'Keep current draft');
  const count=calls.length; await S.poll(); assert.equal(calls.length,count,'failed session reads back off independently');
  W.respond=route=>route==='/sessions'?{sessions:[snapshot('good').session]}:snapshot('good');
  await S.retry();
  assert.equal(S.loadError,null); assert.equal(S.current.native_id,'same-native-good');
  assert.equal(node('ws-message').value,'Keep current draft');
  assert.ok(calls.every(call=>call.body===undefined),'read recovery must never create, cancel, delete or resend');
});

test('native warning blocks only affected conversation and clears on same-ID read reconciliation', async()=>{
  const {W,S,node,drafts,calls}=fixture(); let warning={code:'invalid_transcript',message:'Inspect native file'};
  W.respond=route=>route==='/sessions'?{sessions:[snapshot('bad',warning).session,snapshot('good').session]}:snapshot(route.split('/')[2],route.endsWith('/bad')?warning:null);
  await S.init(); assert.equal(S.current.id,'bad');
  node('ws-message').value='Bad session draft';S.render();assert.equal(node('ws-send').disabled,true);
  assert.match(node('ws-session-notice').textContent,/原生会话记录需要核对/);
  await S.select('good');node('ws-message').value='Healthy session draft';S.render();
  assert.equal(node('ws-send').disabled,false);assert.equal(drafts.get('message:bad').message,'Bad session draft');
  await S.select('bad');warning=null;await S.retry();
  assert.equal(S.current.native_id,'same-native-bad');assert.equal(S.current.native_sync_warning,undefined);
  assert.equal(node('ws-message').value,'Bad session draft');assert.equal(node('ws-send').disabled,false);
  assert.ok(calls.every(call=>call.body===undefined));
});

test('slow initial listing cannot replace a conversation explicitly selected meanwhile',async()=>{
  const {W,S,calls}=fixture();let release;
  W.respond=route=>route==='/sessions'?new Promise(resolve=>{release=resolve;}):snapshot('good');
  const initial=S.init();await flush();await S.select('good');
  release({sessions:[snapshot('old-default').session,snapshot('good').session]});await initial;
  assert.equal(S.current.id,'good');assert.equal(S.current.native_id,'same-native-good');
  assert.ok(!calls.some(call=>call.route==='/sessions/old-default'));
});
