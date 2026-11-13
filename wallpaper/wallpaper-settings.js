"use strict";
// Register immediately, before the page's deferred scene scripts.
(() => {
  const profiles = {
    fast: {hardwarePollMs:250,audioPollMs:33,renderFps:60},
    balanced: {hardwarePollMs:500,audioPollMs:50,renderFps:30},
    quiet: {hardwarePollMs:1000,audioPollMs:100,renderFps:20}
  };
  const state = window.MORNYE_SETTINGS = {
    version:"0.1.4-beta.1",floatEnabled:true,floatStrength:1.5,hoverEnabled:true,
    paneBrightness:1,paneOpacity:1,audioEnabled:true,audioGain:1,
    parallaxEnabled:true,parallaxStrength:1,parallaxResponseMs:240,parallaxReturn:true,
    refreshMode:"fast",fitMode:"contain",generalFps:60,paused:false,
    ...profiles.fast,windows:{}
  };
  const bindings = [{"key": "showwindow01", "id": "threads", "number": 1, "label": "每逻辑处理器负载"}, {"key": "showwindow02", "id": "ram-free", "number": 2, "label": "可用内存 / 缓存"}, {"key": "showwindow03", "id": "transfer", "number": 3, "label": "累计传输"}, {"key": "showwindow04", "id": "stereo", "number": 4, "label": "左右相对能量（音频待接入）"}, {"key": "showwindow05", "id": "bands", "number": 5, "label": "低中高频相对能量（音频待接入）"}, {"key": "showwindow06", "id": "ram-trend", "number": 6, "label": "内存趋势"}, {"key": "showwindow09", "id": "vram", "number": 9, "label": "显存"}, {"key": "showwindow10", "id": "date", "number": 10, "label": "日期"}, {"key": "showwindow11", "id": "cpu", "number": 11, "label": "CPU 使用率 / 温度"}, {"key": "showwindow12", "id": "gpu", "number": 12, "label": "GPU 使用率 / 温度 / 功耗"}, {"key": "showwindow13", "id": "ram", "number": 13, "label": "物理内存"}, {"key": "showwindow14", "id": "net", "number": 14, "label": "上下行网速"}, {"key": "showwindow15", "id": "spectrum", "number": 15, "label": "主频谱"}, {"key": "showwindow16", "id": "clock", "number": 16, "label": "数字时钟"}];
  for(const row of bindings)state.windows[row.id]=true;
  let ready=false;
  const clamp=(value,min,max,fallback)=>{
    const number=Number(value);return Number.isFinite(number)?Math.max(min,Math.min(max,number)):fallback;
  };
  function apply(){
    if(!ready)return;
    const $=id=>document.getElementById(id),stage=$("stage");
    $("float-enabled").checked=state.floatEnabled;
    $("hover-enabled").checked=state.hoverEnabled;
    const strength=$("float-strength");strength.min="0";strength.max="2.5";strength.step="0.05";
    strength.value=String(state.floatStrength);
    document.documentElement.style.setProperty("--window-brightness",String(state.paneBrightness));
    document.documentElement.style.setProperty("--window-opacity",String(state.paneOpacity));
    for(const item of window.SAMPLE_ASSETS.windows){
      const visible=item.id!=="cpu-trend"&&item.id!=="gpu-trend"&&state.windows[item.id]!==false;
      item.editorVisible=visible;
      const content=document.querySelector(`.native-pane[data-window="${item.id}"]`);
      const motion=document.querySelector(`.window-motion[data-window="${item.id}"]`);
      if(content)content.hidden=!visible;
      if(motion)motion.hidden=!visible;
    }
    $("float-enabled").dispatchEvent(new Event("change"));
    $("hover-enabled").dispatchEvent(new Event("change"));
    $("float-strength").dispatchEvent(new Event("input"));
    stage.dispatchEvent(new CustomEvent("fit-change"));
    document.dispatchEvent(new CustomEvent("mornye-settings-change",{detail:state}));
  }
  window.wallpaperPropertyListener = {
    applyUserProperties(properties){
      if(properties.floatenabled)state.floatEnabled=properties.floatenabled.value===true;
      if(properties.floatstrength)state.floatStrength=clamp(properties.floatstrength.value,0,250,150)/100;
      if(properties.parallaxenabled)state.parallaxEnabled=properties.parallaxenabled.value===true;
      if(properties.parallaxstrength)state.parallaxStrength=clamp(properties.parallaxstrength.value,0,200,100)/100;
      if(properties.parallaxresponse)state.parallaxResponseMs=clamp(properties.parallaxresponse.value,100,800,240);
      if(properties.parallaxreturn)state.parallaxReturn=properties.parallaxreturn.value===true;
      if(properties.hoverenabled)state.hoverEnabled=properties.hoverenabled.value===true;
      if(properties.windowbrightness)state.paneBrightness=clamp(properties.windowbrightness.value,30,180,100)/100;
      if(properties.windowopacity)state.paneOpacity=clamp(properties.windowopacity.value,10,100,100)/100;
      if(properties.audioenabled)state.audioEnabled=properties.audioenabled.value===true;
      if(properties.audiogain)state.audioGain=clamp(properties.audiogain.value,25,250,100)/100;
      if(properties.refreshmode&&profiles[properties.refreshmode.value]){
        state.refreshMode=properties.refreshmode.value;Object.assign(state,profiles[state.refreshMode]);
      }
      if(properties.fitmode&&["contain","cover"].includes(properties.fitmode.value))state.fitMode=properties.fitmode.value;
      for(const row of bindings)if(properties[row.key])state.windows[row.id]=properties[row.key].value===true;
      apply();
    },
    applyGeneralProperties(properties){
      if(Number(properties.fps)>0)state.generalFps=clamp(properties.fps,1,240,60);
      apply();
    },
    setPaused(isPaused){state.paused=Boolean(isPaused);apply();}
  };
  document.addEventListener("DOMContentLoaded",()=>{ready=true;apply();},{once:true});
})();
