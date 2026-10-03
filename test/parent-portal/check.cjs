const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../src/web');
const out = process.env.SCREENSHOT_DIR ? path.resolve(process.env.SCREENSHOT_DIR) : null;
if (out) fs.mkdirSync(out, { recursive: true });
// Every account, child, activity, and result is synthetic. No live server,
// repository .env, credentials, real microphone, or production catalog is used.
const catalog = { levels: [{ level: 'D', sublevels: [{ sublevel: '1', exercises: [{
  section: 'standard', exerciseNumber: '1', lines: [{
    line: 'a', type: 'deletion', word: 'sailboat', answer: 'sail'
  }]
}]}]}] };
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const filename = path.join(root, pathname === '/' ? 'index.html' : pathname);
  const relative = path.relative(root, filename);
  if (relative.startsWith('..') || path.isAbsolute(relative) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) { res.writeHead(404); return res.end(); }
  res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(filename));
});
const configs = { enabled:true, practice_mode:'account', registration_open:true, child_collection_enabled:true, privacy_notice_version:'v1', privacy_contact:'privacy@example.invalid,review@example.invalid', retention_days:365 };
const guestConfig = { enabled:false, practice_mode:'guest', registration_open:false, child_collection_enabled:false };
const parentFixture = { id:'parent1', email:'synthetic-parent@example.invalid', consent_status:'verified', csrf_token:'synthetic-csrf', deletion_pending:false, children:[{id:'child1',nickname:'Robin'},{id:'child2',nickname:'Wren'}] };
(async () => {
 await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
 const url = `http://127.0.0.1:${server.address().port}`;
 const browser = await chromium.launch({
   ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
   headless:true,
   args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']
 });
 try {
  const results = [];
  async function setup({config={}, parent=parentFixture, width=390}={}) {
    const context = await browser.newContext({viewport:{width,height:900}, permissions:['microphone'],acceptDownloads:true});
    await context.route('**/*', route => new URL(route.request().url()).origin === url ? route.continue() : route.abort());
    const page = await context.newPage();
    let state = parent ? structuredClone(parent) : null;
    const errors=[], calls=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.addInitScript(() => { window.micCalls=0; const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia=async(...args)=>{window.micCalls++; const stream=await original(...args); window.testStreams ||= []; window.testStreams.push(stream); return stream;}; });
    await page.route('**/api/**', async route => {
      const req=route.request(), p=new URL(req.url()).pathname, method=req.method();
      const data=req.postData();
      calls.push({p,method,data,headers:req.headers(),url:req.url()});
      const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
      if(p==='/api/account/config')return respond({...configs,...config});
      if(p==='/api/account/me')return respond(state || {detail:'Sign in'},state?200:401);
      if(p==='/api/auth/login') {
        state={...structuredClone(parentFixture),consent_status:'none',children:[]};
        return route.fulfill({status:302,headers:{Location:'/'}});
      }
      if(p==='/api/activities/catalog')return respond(catalog);
      if(p==='/api/activities/current')return respond({type:'deletion',word:'sailboat',answer:'sail',tokens:[{id:'s1',sound:'sail',action:'none'},{id:'s2',sound:'boat',action:'delete'}],deleteTokenIds:['s2']});
      if(p.endsWith('/progress'))return respond({summary:{attempts:3,completed:1,correct:1},attempts:[{id:'a1',created_at:'2026-10-03T15:00:00Z',outcome:'completed',correct:true,selection:{level:'1',exercise:'1'}},{id:'a2',created_at:'2026-10-03T16:00:00Z',outcome:'microphone_unavailable',correct:null,selection:{level:'1',exercise:'1'}},{id:'a3',created_at:'2026-10-03T16:30:00Z',outcome:'technical_error',correct:null,selection:{level:'1',exercise:'1'}}]});
      if(p==='/api/account/consent'){state.consent_status='pending';return respond({status:'pending'});}
      if(p==='/api/account/children'&&method==='POST'){const child={id:'child3',nickname:JSON.parse(data).nickname};state.children.push(child);return respond(child);}
      if(p==='/api/account/export')return respond({parent:state});
      if(p.startsWith('/api/account/children/')&&method==='DELETE'){state.children=state.children.filter(c=>!p.endsWith(c.id));return respond({status:'pending'});}
      if(p==='/api/account/consent/withdraw'){state.consent_status='withdrawn';state.children=[];return respond({status:'pending'});}
      if(p==='/api/auth/logout'){state=null;return respond({status:'ok'});}
      if(p==='/api/account'&&method==='DELETE'){state.deletion_pending=true;return respond({status:'pending'});}
      if(p==='/api/practice/sessions')return respond({id:'session1'});
      if(p==='/api/practice/attempts')return respond({id:'attempt1'});
      if(p.endsWith('/finish'))return respond({status:'ok'});
      if(p==='/api/speech/listen-check')return respond({mode:new URL(req.url()).searchParams.get('mode'),speechDetected:true,correct:true,scores:{accuracy:99}});
      if(p.startsWith('/api/speech/'))return route.fulfill({status:200,headers:{'Content-Type':'audio/wav','X-Arthur-Text':'A practice instruction'},body:Buffer.alloc(44)});
      return respond({detail:'Unexpected route'},404);
    });
    await page.goto(url);
    await page.waitForFunction(()=>window.ParentAccount && document.querySelector('#accountBadge').textContent!=='Checking access');
    await page.waitForFunction(()=>document.querySelector('#levelSelect').options[0]?.textContent!=='Loading...');
    return {page,context,errors,calls};
  }
  for(const [name,options] of [['closed',{config:{enabled:false,practice_mode:'unavailable'}}],['no explicit guest mode',{config:{enabled:false,practice_mode:undefined}}],['signed out',{parent:null}],['guest cannot bypass accounts',{config:{practice_mode:'guest'},parent:null}],['pending',{parent:{...parentFixture,consent_status:'pending'}}],['unverified',{parent:{...parentFixture,consent_status:'none'}}],['collection closed',{config:{child_collection_enabled:false}}]]) {
    const {page,context,errors}=await setup(options);
    assert(await page.locator('#startButton').isDisabled(),`${name} start locked`);
    assert.equal(await page.evaluate(async()=>{try{await getMicStream();return false;}catch{return true;}}),true);
    assert.equal(await page.evaluate(()=>window.micCalls),0);
    if(name==='signed out') {
      assert.equal(await page.locator('#parentLogin').getAttribute('href'),'/api/auth/login');
      await page.locator('#parentLogin').click();
      await page.waitForFunction(()=>document.querySelector('#accountBadge').textContent==='Consent required');
      assert(await page.locator('#startButton').isDisabled(),'mock sign-in alone does not enable collection');
      assert.equal(await page.evaluate(()=>window.micCalls),0);
    }
    assert.deepEqual(errors,[]);
    if(out && name==='closed') await page.screenshot({path:path.join(out,'parent-accounts-closed-mobile.png'),fullPage:true});
    await context.close(); results.push(`${name}: microphone and start blocked`);
  }
  for(const width of [320,390,768,1440]) {
    const {page,context,errors,calls}=await setup({width,config:guestConfig,parent:null});
    await page.waitForFunction(()=>ParentAccount.guest && !document.querySelector('#startButton').disabled);
    assert(await page.locator('#parentAccount').isHidden(),'guest practice does not show the unfinished parent portal');
    assert(await page.locator('#accountAccess').isVisible(),'guest header shows the account placeholders');
    assert.equal(await page.evaluate(()=>window.micCalls),0,'the microphone stays off before a speaking turn');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width} guest layout has no overflow`);
    assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);
    const guestUrl=page.url(), beforeControls=calls.length;
    for(const id of ['#headerSignIn','#headerSignUp']) {
      const button=page.locator(id);
      assert.equal(await button.getAttribute('aria-disabled'),'true');
      assert.equal(await button.evaluate(el=>el.tagName),'BUTTON');
      assert.equal(await button.getAttribute('type'),'button');
      await button.hover();
      await page.waitForFunction(()=>{
        const style=getComputedStyle(document.querySelector('#accountSetupTooltip'));
        return style.visibility==='visible' && Number(style.opacity)>0;
      });
      assert.match(await page.locator('#accountSetupTooltip').innerText(),/account setup.*not complete/i);
      await button.click();
      assert.equal(page.url(),guestUrl,'the placeholder does not navigate');
      await page.mouse.move(0,899);
      await button.focus();
      await page.waitForFunction(()=>{
        const style=getComputedStyle(document.querySelector('#accountSetupTooltip'));
        return style.visibility==='visible' && Number(style.opacity)>0;
      });
      await button.press('Enter');
      await button.press('Space');
      assert.equal(page.url(),guestUrl,'keyboard activation is inert');
    }
    assert.equal(calls.length,beforeControls,'placeholder controls make no requests');
    if(out && (width===390||width===1440)) await page.screenshot({path:path.join(out,`guest-practice-${width}.png`),fullPage:true});
    // Stub playback but retain its API requests and track that no microphone
    // remains active during instructions, token sounds, or feedback audio.
    await page.evaluate(()=>{
      window.microphoneDuringPlayback=[];
      playAudioBlob=async()=>{
        assertPractice();
        window.microphoneDuringPlayback.push(window.testStreams?.some(s=>s.getTracks().some(t=>t.readyState==='live')) || false);
      };
    });
    await page.locator('#startButton').click();
    await page.waitForFunction(()=>window.micCalls===1 && activeSpeechCapture!==null);
    assert(calls.some(c=>c.p==='/api/activities/current'),'guest Start loads an activity');
    assert(calls.some(c=>c.p.startsWith('/api/speech/')&&c.p!=='/api/speech/listen-check'),'guest Start plays an instruction');
    if(width===390) {
      await page.waitForFunction(()=>slidersUnlocked===true,undefined,{timeout:15000});
      assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
      await page.locator('.token').nth(0).focus();await page.keyboard.press('Enter');
      await page.locator('.token').nth(1).focus();await page.keyboard.press('Enter');
      await page.waitForFunction(()=>document.querySelector('#startButton').disabled===false,undefined,{timeout:15000});
      const uploads=calls.filter(c=>c.p==='/api/speech/listen-check');
      assert.equal(uploads.length,2);
      assert.deepEqual(uploads.map(c=>new URL(c.url).searchParams.get('expected')),['sailboat','sail'],'guest speech checks use curriculum words');
      assert(uploads.every(c=>!new URL(c.url).searchParams.has('attempt_id')&&!c.headers['x-csrf-token']),'guest audio has no saved attempt or account CSRF token');
      assert.equal(await page.evaluate(()=>guestAttemptActive),false,'completed guest attempts release in-memory state');
    } else {
      await page.evaluate(()=>resetActivitySelection());
      assert(!calls.some(c=>c.p==='/api/speech/listen-check'),'canceling before the speaking window ends never uploads audio');
      assert.equal(await page.evaluate(()=>guestAttemptActive),false,'canceled guest attempts release in-memory state');
    }
    assert.equal(await page.evaluate(()=>activeSpeechCapture),null);
    assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
    assert.equal(await page.evaluate(()=>window.microphoneDuringPlayback.some(Boolean)),false,'the microphone stays off during playback');
    assert(!calls.some(c=>(c.p.startsWith('/api/account/')&&c.p!=='/api/account/config')||c.p.startsWith('/api/practice/')),'guest practice never reads parent profiles or writes progress');
    assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);
    assert.deepEqual(errors,[]);await context.close();
    results.push(`${width}px guest practice: responsive layout, inert accessible account placeholders, no account/progress requests, microphone limited to speaking turns`);
  }
  for(const width of [320,390,768,1440]) {
    const {page,context,errors,calls}=await setup({width});
    assert(await page.locator('#startButton').isDisabled());
    await page.locator('#childSelect').selectOption('child1');
    await page.waitForFunction(()=>!document.querySelector('#startButton').disabled);
    await page.waitForSelector('#progressRows tr');
    assert.equal(await page.locator('#progressRows tr').count(),3);
    assert(await page.locator('#progressRows').innerText().then(t=>t.includes('Microphone unavailable')));
    assert(await page.locator('#progressRows').innerText().then(t=>t.includes('Speech check unavailable')));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width} no overflow`);
    assert.deepEqual(await page.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);
    if(out && (width===390||width===1440))await page.screenshot({path:path.join(out,`parent-accounts-${width}.png`),fullPage:true});
    if(width===390) {
      await page.locator('#deleteChildButton').click();
      assert(await page.locator('#deleteDialog').isVisible());
      await page.locator('#cancelDelete').click();
      assert(!calls.some(c=>c.method==='DELETE'));
      await page.locator('#deleteChildButton').click();
      await page.locator('#deleteConfirmation').fill('DELETE');
      await page.locator('#confirmDelete').click();
      // Closing the dialog happens before the account refresh finishes. Wait for
      // the refreshed children and the operation's finally block, not animation.
      await page.waitForFunction(()=>
        !document.querySelector('#deleteDialog').open &&
        !document.querySelector('#childSelect option[value="child1"]') &&
        document.querySelector('#childSelect').options.length===2 &&
        !document.querySelector('#confirmDelete').disabled
      );
      assert(calls.some(c=>c.p==='/api/account/children/child1'&&c.method==='DELETE'&&c.headers['x-csrf-token']==='synthetic-csrf'));
      assert(await page.locator('#startButton').isDisabled());
      assert.equal(await page.locator('#childSelect option').count(),2);
      const beforeAdd = calls.filter(c=>c.p==='/api/account/children'&&c.method==='POST').length;
      await page.locator('#childForm button').click();
      assert.equal(calls.filter(c=>c.p==='/api/account/children'&&c.method==='POST').length,beforeAdd,'required nickname prevents empty submission');
      assert.equal(await page.locator('#childNickname').evaluate(el=>el.validity.valueMissing),true);
      await page.locator('#childNickname').fill('   ');
      await page.locator('#childForm button').click();
      assert.equal(await page.locator('#childNickname').evaluate(el=>el.validity.customError),true,'whitespace-only nickname has visible validation');
      assert.equal(calls.filter(c=>c.p==='/api/account/children'&&c.method==='POST').length,beforeAdd);
      await page.locator('#childNickname').fill('');
      assert.equal(await page.locator('#childNickname').getAttribute('maxlength'),'40');
      await page.locator('#childNickname').pressSequentially('x'.repeat(45));
      assert.equal((await page.locator('#childNickname').inputValue()).length,40,'native maxlength limits typed nickname');
      const unsafeNickname='<svg onload=window.injected=1>';
      await page.locator('#childNickname').fill(unsafeNickname);
      await page.locator('#childForm button').click();
      await page.waitForFunction(()=>
        document.querySelector('#childSelect').value==='child3' &&
        !document.querySelector('#childForm button').disabled &&
        ParentAccount.ready
      );
      assert(await page.locator('#childSelect').innerText().then(t=>t.includes(unsafeNickname)));
      assert.equal(await page.evaluate(()=>window.injected),undefined,'nickname is literal text and cannot run markup');
      assert.equal(await page.locator('#childSelect svg').count(),0);
      await page.locator('.parent-controls summary').click();
      const downloadPromise=page.waitForEvent('download');
      await page.locator('#exportButton').click();
      assert.equal((await downloadPromise).suggestedFilename(),'reading-sound-games-family-data.json');
      await page.waitForFunction(()=>!document.querySelector('#exportButton').disabled);
      await page.locator('#withdrawButton').click();
      await page.locator('#deleteConfirmation').fill('DELETE');
      await page.locator('#confirmDelete').click();
      await page.waitForFunction(()=>
        !document.querySelector('#deleteDialog').open &&
        document.querySelector('#childSelect').options.length===1 &&
        document.querySelector('#accountBadge').textContent==='Consent required' &&
        !document.querySelector('#confirmDelete').disabled
      );
      assert(await page.locator('#startButton').isDisabled());
      results.push('Parent child delete/add/export/withdraw controls, native nickname constraints, XSS safety, and CSRF passed');
    }
    assert.deepEqual(errors,[]);await context.close();
  }
  {
    const {page,context,errors,calls}=await setup({parent:{...parentFixture,consent_status:'none',children:[]}});
    await page.locator('#adultConfirmed').check();await page.locator('#consentForm button').click();
    await page.waitForFunction(()=>
      document.querySelector('#accountBadge').textContent==='Verification pending' &&
      !document.querySelector('#consentForm button').disabled
    );
    assert(await page.locator('#childPanel').isHidden());assert(await page.locator('#startButton').isDisabled());
    assert(calls.some(c=>c.p==='/api/account/consent'&&JSON.parse(c.data).notice_version==='v1'));
    assert.deepEqual(errors,[]);await context.close();results.push('Consent request never activates child collection');
  }
  {
    const {page,context,errors,calls}=await setup();
    await page.locator('#childSelect').selectOption('child1');
    await page.waitForFunction(()=>!document.querySelector('#startButton').disabled);
    await page.evaluate(()=>{playAudioBlob=async()=>{assertPractice();};});
    await page.locator('#startButton').click();
    await page.waitForFunction(()=>window.micCalls>0 && activeSpeechCapture!==null);
    await page.locator('#childSelect').selectOption('child2');
    await page.waitForFunction(()=>window.testStreams?.length>0 && window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended')));
    assert.equal(await page.evaluate(()=>activeSpeechCapture),null);
    assert(!calls.some(c=>c.p==='/api/speech/listen-check'));
    await page.locator('#startButton').click();
    await page.waitForFunction(()=>window.micCalls===2 && activeSpeechCapture!==null);
    await page.locator('#logoutButton').click();
    await page.waitForFunction(()=>window.testStreams?.length===2 && window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended')));
    await page.waitForFunction(()=>!document.querySelector('#signedOutPanel').hidden && !document.querySelector('#logoutButton').disabled);
    assert(await page.locator('#startButton').isDisabled());
    assert.deepEqual(errors,[]);await context.close();results.push('Fake microphone turns off on child switch and logout; canceled audio never uploads');
  }
  {
    const {page,context,errors,calls}=await setup();
    await page.locator('#childSelect').selectOption('child1');
    await page.waitForFunction(()=>!document.querySelector('#startButton').disabled);
    await page.evaluate(()=>{playAudioBlob=async()=>{assertPractice();};});
    await page.locator('#startButton').click();
    await page.waitForFunction(()=>slidersUnlocked===true,undefined,{timeout:15000});
    assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
    await page.locator('.token').nth(0).focus();await page.keyboard.press('Enter');
    await page.locator('.token').nth(1).focus();await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('#startButton').disabled===false,undefined,{timeout:15000});
    const uploads=calls.filter(c=>c.p==='/api/speech/listen-check');
    assert.equal(uploads.length,2);
    assert(uploads.every(c=>!new URL(c.url).searchParams.has('expected')&&new URL(c.url).searchParams.get('attempt_id')==='attempt1'&&c.headers['x-csrf-token']==='synthetic-csrf'));
    assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
    assert.deepEqual(errors,[]);await context.close();results.push('Complete practice presence/final lifecycle: server attempt ID + CSRF, no expected text, mic closed after speaking');
  }
  {
    const {page,context,errors,calls}=await setup();
    await page.route('**/api/speech/listen-check?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({mode:'presence',speechDetected:false,correct:null,scores:{}})}));
    await page.locator('#childSelect').selectOption('child1');
    await page.waitForFunction(()=>!document.querySelector('#startButton').disabled);
    await page.evaluate(()=>{playAudioBlob=async()=>{assertPractice();};});
    await page.locator('#startButton').click();
    await page.waitForFunction(()=>document.querySelector('#subtitleBox').textContent.startsWith('No speech was detected.'));
    assert(await page.locator('#startButton').isEnabled());
    assert.equal(await page.evaluate(()=>practiceAttemptId),null);
    assert.equal(await page.evaluate(()=>slidersUnlocked),false);
    assert.deepEqual(errors,[]);await context.close();results.push('No-speech result ends attempt neutrally and offers a new start');
  }
  {
    const {page,context,errors}=await setup();
    await page.route('**/api/account/expired',route=>route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({detail:'Please sign in again to manage privacy settings.'})}));
    await page.locator('#childSelect').selectOption('child1');
    await page.waitForFunction(()=>!document.querySelector('#startButton').disabled);
    await page.evaluate(()=>{playAudioBlob=async()=>{assertPractice();};});
    await page.locator('#startButton').click();
    await page.waitForFunction(()=>window.testStreams?.length===1 && activeSpeechCapture!==null);
    await page.evaluate(()=>ParentAccount.request('/api/account/expired').catch(()=>{}));
    assert(await page.locator('#startButton').isDisabled());
    assert(await page.locator('#parentReauthenticate').isVisible());
    assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
    assert.deepEqual(errors,[]);await context.close();results.push('Failed authorization cancels active microphone and prompts reauthentication');
  }
  {
    const {page,context,errors}=await setup({config:{child_collection_enabled:false}});
    await page.locator('#childSelect').selectOption('child1');
    await page.waitForSelector('#progressRows tr');
    assert(await page.locator('#startButton').isDisabled());
    assert(await page.locator('#childForm').isHidden());
    assert(await page.locator('#deleteChildButton').isVisible());
    await page.goto(`${url}/privacy.html`);
    await page.waitForFunction(()=>document.querySelector('#privacyContactDetails').textContent.includes('privacy@example.invalid'));
    assert.equal(await page.getByRole('link',{name:'Email both privacy contacts'}).getAttribute('href'),'mailto:privacy%40example.invalid,review%40example.invalid');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);await context.close();results.push('Review/delete access remains available with collection closed; privacy page contacts render');
  }
  console.log(JSON.stringify({passed:results},null,2));
 } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
