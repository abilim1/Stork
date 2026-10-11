// Canvas rendering stays local: changing the view never launches AI research.
export class PriceChart {
  constructor(canvas, tooltip, empty) {
    this.canvas = canvas; this.tooltip = tooltip; this.empty = empty;
    this.ctx = canvas.getContext('2d'); this.rows = []; this.style = 'line';
    this.timezone = 'America/New_York'; this.start = 0; this.count = 200; this.hover = -1;
    this.observer = new ResizeObserver(() => this.draw()); this.observer.observe(canvas);
    canvas.addEventListener('wheel', e => {
      e.preventDefault(); if (!this.rows.length) return;
      const old = this.count;
      this.count = Math.min(this.rows.length, Math.max(8, Math.round(old * (e.deltaY > 0 ? 1.18 : .84))));
      this.start = Math.max(0, Math.min(this.rows.length - this.count, this.start + Math.round((old - this.count) / 2)));
      this.draw();
    }, {passive:false});
    canvas.addEventListener('pointerdown', e => {this.drag = {x:e.clientX, start:this.start}; canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointerup', () => {this.drag = null;});
    canvas.addEventListener('pointercancel', () => {this.drag = null;});
    canvas.addEventListener('pointerleave', () => {this.hover = -1; this.tooltip.hidden = true; this.draw();});
    canvas.addEventListener('pointermove', e => this.pointer(e));
    canvas.addEventListener('dblclick', () => this.reset());
    canvas.addEventListener('keydown', e => {
      if (['ArrowLeft','ArrowRight','+','-','Home'].includes(e.key)) e.preventDefault();
      if (e.key === 'Home') return this.reset();
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') this.start = Math.max(0, Math.min(this.rows.length - this.count, this.start + (e.key === 'ArrowLeft' ? -10 : 10)));
      if (e.key === '+' || e.key === '-') {this.count = Math.min(this.rows.length, Math.max(8, Math.round(this.count * (e.key === '+' ? .8 : 1.2)))); this.start = Math.max(0, Math.min(this.start, this.rows.length-this.count));}
      this.draw();
    });
  }
  setData(rows, reset = false) {
    const previousLength = this.rows.length;
    const atEnd = this.start + this.count >= previousLength - 1;
    const fitted = this.start === 0 && this.count >= previousLength;
    this.rows = rows;
    if (reset || !previousLength || fitted) this.reset();
    else {
      this.count = Math.min(this.count, rows.length);
      this.start = atEnd ? Math.max(0, rows.length-this.count) : Math.min(this.start, Math.max(0, rows.length-this.count));
      this.hover = -1;
      this.tooltip.hidden = true;
      this.draw();
    }
  }
  reset() {this.count = this.rows.length; this.start = 0; this.hover = -1; this.tooltip.hidden = true; this.draw();}
  pointer(e) {
    if (!this.rows.length || !this.geometry) return;
    const r = this.canvas.getBoundingClientRect();
    if (this.drag) {
      this.start = Math.max(0, Math.min(this.rows.length-this.count, this.drag.start - Math.round((e.clientX-this.drag.x)/this.geometry.step)));
      this.hover = -1; this.tooltip.hidden = true; this.draw(); return;
    }
    const index = Math.floor((e.clientX-r.left-this.geometry.left)/this.geometry.step);
    this.hover = Math.max(0,Math.min(this.visible.length-1,index));
    const b = this.visible[this.hover]; if (!b) return;
    const date = new Intl.DateTimeFormat('en-US',{timeZone:this.timezone,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(b.time*1000));
    const lines = [date + ' · '+b.session, `O ${b.open.toFixed(2)}   H ${b.high.toFixed(2)}`, `L ${b.low.toFixed(2)}   C ${b.close.toFixed(2)}`, `Volume ${Math.round(b.volume).toLocaleString()}`];
    this.tooltip.replaceChildren(...lines.map(text => {const div=document.createElement('div'); div.textContent=text; return div;}));
    this.tooltip.hidden = false;
    this.tooltip.style.left = Math.min(Math.max(6,e.clientX-r.left+16),r.width-205)+'px';
    this.tooltip.style.top = Math.max(5,Math.min(e.clientY-r.top-90,r.height-115))+'px';
    this.draw();
  }
  draw() {
    const rect = this.canvas.getBoundingClientRect(); if (!rect.width) return;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(rect.width*ratio); this.canvas.height = Math.round(rect.height*ratio);
    const c=this.ctx; c.scale(ratio,ratio); c.clearRect(0,0,rect.width,rect.height);
    const css = getComputedStyle(document.documentElement);
    const color = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    const grid = color('--chart-grid', '#edf1f3');
    const axis = color('--chart-axis', '#768492');
    const green = color('--green', '#168e79'), red = color('--red', '#cb7979');
    const sessionFill = color('--chart-session', '#f8fafb');
    const volumeUp = color('--chart-volume-up', '#d5e9e2'), volumeDown = color('--chart-volume-down', '#efdddd');
    this.empty.hidden = !!this.rows.length;
    if (!this.rows.length) {this.empty.textContent='No candles for this view. Choose a wider interval or another session.'; return;}
    const visible=this.rows.slice(this.start,this.start+this.count); this.visible=visible;
    if (!visible.length) return;
    const left=18,right=65,top=27,bottom=rect.height-78;
    const width=rect.width-left-right, height=bottom-top;
    // Iteration avoids the argument limit when displaying a long tick-derived archive.
    let min=Infinity,max=-Infinity,maxVol=1;
    for (const bar of visible) {min=Math.min(min,bar.low);max=Math.max(max,bar.high);maxVol=Math.max(maxVol,bar.volume);}
    const padding=(max-min||max*.005)*.14; min-=padding;max+=padding;
    const step=width/visible.length, x=i=>left+(i+.5)*step, y=p=>top+(max-p)/(max-min)*height;
    this.geometry={step,left}; c.font='9px -apple-system, Segoe UI, sans-serif';c.textBaseline='middle';
    // Extended sessions are lightly shaded while gaps remain compressed in trading-time order.
    visible.forEach((b,i)=>{if(b.session!=='regular'){c.fillStyle=sessionFill;c.fillRect(left+i*step,top,step,height+48);}});
    for(let i=0;i<5;i++) {const value=min+(max-min)*i/4, yy=y(value); c.strokeStyle=grid;c.lineWidth=1;c.setLineDash([3,4]);c.beginPath();c.moveTo(left,yy);c.lineTo(left+width,yy);c.stroke();c.setLineDash([]);c.fillStyle=axis;c.fillText(value.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}),left+width+12,yy);}
    if(this.style==='line'||this.style==='area') {
      const points=visible.map((b,i)=>[x(i),y(b.close)]);
      if(this.style==='area') {const gradient=c.createLinearGradient(0,top,0,bottom);gradient.addColorStop(0,green+'30');gradient.addColorStop(1,green+'00');c.fillStyle=gradient;c.beginPath();c.moveTo(points[0][0],bottom);points.forEach(p=>c.lineTo(...p));c.lineTo(points.at(-1)[0],bottom);c.closePath();c.fill();}
      c.strokeStyle=green;c.lineWidth=1.8;c.lineJoin='round';c.beginPath();points.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.stroke();
    } else {
      const bodyWidth=Math.max(1,Math.min(9,step*.58));
      visible.forEach((b,i)=>{const xx=x(i),color=b.close>=b.open?green:red;c.strokeStyle=color;c.fillStyle=color;c.lineWidth=1;c.beginPath();c.moveTo(xx,y(b.high));c.lineTo(xx,y(b.low));c.stroke();
        if(this.style==='candles')c.fillRect(xx-bodyWidth/2,Math.min(y(b.open),y(b.close)),bodyWidth,Math.max(1,Math.abs(y(b.open)-y(b.close))));
        else{c.beginPath();c.moveTo(xx-bodyWidth/2,y(b.open));c.lineTo(xx,y(b.open));c.moveTo(xx,y(b.close));c.lineTo(xx+bodyWidth/2,y(b.close));c.stroke();}
      });
    }
    visible.forEach((b,i)=>{const h=b.volume/maxVol*32;c.fillStyle=b.close>=b.open?volumeUp:volumeDown;c.fillRect(left+i*step+step*.15,bottom+45-h,Math.max(1,step*.7),h);});
    const firstDate=visible[0].date,lastDate=visible.at(-1).date;
    const timeFormat=new Intl.DateTimeFormat('en-US',{timeZone:this.timezone,...(firstDate===lastDate?{hour:'2-digit',minute:'2-digit',hour12:false}:{month:'short',day:'numeric'})});
    const labels=Math.max(2,Math.floor(width/110));c.fillStyle=axis;c.textAlign='center';
    for(let i=0;i<labels;i++){const index=Math.round(i*(visible.length-1)/(labels-1));c.fillText(timeFormat.format(new Date(visible[index].time*1000)),x(index),rect.height-14);}
    c.textAlign='left';const last=visible.at(-1),lastY=y(last.close);c.setLineDash([3,3]);c.strokeStyle=green+'60';c.beginPath();c.moveTo(left,lastY);c.lineTo(left+width,lastY);c.stroke();c.setLineDash([]);c.fillStyle=green;c.fillRect(left+width+5,lastY-9,58,18);c.fillStyle=color('--chart-tag-ink','#fff');c.font='9px -apple-system, Segoe UI, sans-serif';c.fillText(last.close.toFixed(2),left+width+9,lastY);
    if(this.hover>=0&&visible[this.hover]){const b=visible[this.hover];c.strokeStyle='#99aaa9';c.setLineDash([3,3]);c.beginPath();c.moveTo(x(this.hover),top);c.lineTo(x(this.hover),bottom+45);c.stroke();c.beginPath();c.moveTo(left,y(b.close));c.lineTo(left+width,y(b.close));c.stroke();c.setLineDash([]);c.fillStyle=green;c.beginPath();c.arc(x(this.hover),y(b.close),3,0,Math.PI*2);c.fill();}
  }
}
