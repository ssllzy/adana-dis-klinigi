const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(__dirname + '/hero.js', 'utf8');

function setup({reduced=false, motion=true, failure=false}={}) {
  const events = new Map();
  const timers = [];
  const classes = new Set(['intro-pending']);
  const element = () => ({style:{removeProperty(){}},addEventListener(name,fn){events.set(name,fn)},getBoundingClientRect(){return {width:500,height:160,left:60,top:250}}});
  const title=element(), photo=element(), skip=element(), detail=element();
  const nodes={'.cinema-hero':element(),'.cinema-title':title,'.cinema-photo':photo,'.intro-skip':skip};
  const preference={matches:reduced,addEventListener(name,fn){events.set('preference',fn)},removeEventListener(){}};
  const calls=[];
  const context={document:{documentElement:{classList:{contains:n=>classes.has(n),remove:n=>classes.delete(n)}},querySelector:s=>nodes[s],querySelectorAll:()=>[detail],fonts:{ready:Promise.resolve()},addEventListener(name,fn){events.set(name,fn)},removeEventListener(){}},matchMedia:()=>preference,innerWidth:1280,innerHeight:800,scrollY:0,setTimeout(fn){timers.push(fn);return timers.length},clearTimeout(){}};
  context.window={addEventListener(name,fn){events.set(name,fn)},removeEventListener(){}};
  if(motion) context.Motion=context.window.Motion={animate(el,frames,options){if(failure)throw Error('Animation unavailable');const control={stop(){},then(resolve){control.resolve=resolve}};calls.push({el,frames,options,control});return control;}};
  vm.runInNewContext(source,context);
  return {classes,events,calls,timers};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('reduced motion and missing library leave content accessible',()=>{
  for(const options of [{reduced:true},{motion:false}]) assert.equal(setup(options).classes.has('intro-pending'),false);
});
test('animation failure releases the intro',async()=>{
  const app=setup({failure:true});await flush();assert.equal(app.classes.has('intro-pending'),false);
});
test('resize, preference change and keyboard release an active intro',async()=>{
  for(const [event,arg] of [['resize'],['preference'],['keydown',{key:'Tab'}],['keydown',{key:'Escape'}]]){
    const app=setup();await flush();assert.equal(app.calls.length,1);app.events.get(event)(arg);assert.equal(app.classes.has('intro-pending'),false);
  }
});
test('slogan resolves before sliding, then photo zoom and supporting content appear',async()=>{
  const app=setup();await flush();assert.deepEqual(Array.from(app.calls[0].frames.opacity),[0,1]);
  app.calls[0].control.resolve();await flush();assert.ok(app.calls[1].frames.transform);
  app.calls[1].control.resolve();await flush();assert.equal(app.calls[2].frames.transform[1],'scale(1.035)');
  app.calls[3].control.resolve();await flush();app.calls[2].control.resolve();await flush();assert.equal(app.classes.has('intro-pending'),false);
});
