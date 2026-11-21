import RFB from './vendor/novnc/core/rfb.js';
import Keyboard from './vendor/novnc/core/input/keyboard.js';

// Keep composition local; send committed Unicode rather than IME placeholder keys.
export function desktopSurface(target,url,activity=()=>{}) {
  const rfb=new RFB(target,url,{shared:true});
  rfb.resizeSession=true; rfb.scaleViewport=false; rfb.focusOnClick=false;
  rfb.qualityLevel=9; rfb.compressionLevel=2;
  const input=document.createElement('textarea');
  input.setAttribute('aria-label','Native text input'); input.autocomplete='off';input.spellcheck=false;
  Object.assign(input.style,{position:'absolute',width:'1px',height:'1px',opacity:'.01',left:'2px',top:'2px',resize:'none'});
  target.append(input);
  let composing=false,ignoreInput=null,locked=false;const down=new Map();
  const keyboard=new Keyboard(input);
  keyboard.onkeyevent=(keysym,code,pressed)=>{
    if(locked||composing)return;
    if(pressed)down.set(code,keysym);else down.delete(code);
    rfb.sendKey(keysym,code,pressed);activity({});
  };
  input.addEventListener('keydown',event=>{if(composing||event.isComposing||event.keyCode===229)event.stopImmediatePropagation();},true);
  input.addEventListener('keyup',event=>{if(composing||event.isComposing||event.keyCode===229)event.stopImmediatePropagation();},true);
  keyboard.grab();
  const text=(value)=>{
    if(locked)return;
    for(const char of value){const point=char.codePointAt(0);rfb.sendKey(point===10?0xff0d:point>255?0x01000000+point:point,null);}
    activity({});input.value='';
  };
  input.addEventListener('compositionstart',()=>{composing=true;activity({composing:true});});
  input.addEventListener('compositionend',event=>{composing=false;text(event.data||'');ignoreInput=event.data;activity({composing:false});});
  input.addEventListener('input',event=>{
    if(composing||event.isComposing)return;
    if(ignoreInput!==null&&event.data===ignoreInput){ignoreInput=null;input.value='';return;}
    ignoreInput=null;if(event.data)text(event.data);
  });
  const focus=()=>{if(!locked)queueMicrotask(()=>input.focus({preventScroll:true}));};
  target.addEventListener('pointerdown',focus,true);
  const release=()=>{
    for(const [code,key]of down)rfb.sendKey(key,code,false);
    down.clear();composing=false;activity({release:true});
  };
  window.addEventListener('blur',release);input.addEventListener('blur',release);
  rfb.addEventListener('connect',()=>{target.dataset.connected='true';focus();});
  rfb.addEventListener('disconnect',()=>{target.dataset.connected='false';release();});
  return {rfb,input,paste:text,release,
    setLocked(value){locked=value;rfb.viewOnly=value;if(value){release();input.blur();}},
    close(){release();keyboard.ungrab();window.removeEventListener('blur',release);target.removeEventListener('pointerdown',focus,true);rfb.disconnect();input.remove();}
  };
}
