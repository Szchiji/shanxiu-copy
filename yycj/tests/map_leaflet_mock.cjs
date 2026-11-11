function createLeaflet(){
 const L={maps:[],markers:[],tiles:[]};
 function events(obj){obj.events={};obj.on=function(names,fn){for(const n of names.split(' '))(this.events[n]??=new Set()).add(fn);return this};obj.fire=function(n){for(const f of this.events[n]||[])f();return this};return obj}
 L.map=(id,opts)=>{const m=events({id,opts,zoom:undefined,box:null,removed:false,project(p,z){const scale=256*Math.pow(2,z);return{x:p[1]/360*scale,y:p[0]/360*scale}},getZoom(){return this.zoom},getBounds(){const b=this.box;return{getSouth:()=>b.south,getWest:()=>b.west,getNorth:()=>b.north,getEast:()=>b.east}},fitBounds(pts,opt){const lats=pts.map(x=>x[0]),lngs=pts.map(x=>x[1]);this.zoom=opt.maxZoom;this.box={south:Math.min(...lats)-.1,north:Math.max(...lats)+.1,west:Math.min(...lngs)-.1,east:Math.max(...lngs)+.1};this.fire('moveend');return this},invalidateSize(){this.invalidates=(this.invalidates||0)+1;},getCenter(){const b=this.box||{};return{lat:((b.south||0)+(b.north||0))/2,lng:((b.west||0)+(b.east||0))/2};},setView(p,z){this.zoom=z;const c=p||[];this.box={south:(c[0]||0)-.1,north:(c[0]||0)+.1,west:(c[1]||0)-.1,east:(c[1]||0)+.1};return this;},remove(){this.removed=true},move(box,z){this.box=box;if(z!=null)this.zoom=z;this.fire('moveend');}});L.maps.push(m);return m};
 L.control={zoom:()=>({addTo(){return this}})};L.layerGroup=()=>({layers:[],addTo(){return this},clearLayers(){this.layers=[]}});
 L.tileLayer=()=>{const t=events({redraws:0,redraw(){this.redraws++;this.fire('loading');return this},addTo(){return this}});L.tiles.push(t);return t};L.divIcon=x=>x;
 L.marker=(p,opts)=>{const m=events({p,opts,addTo(layer){layer.layers.push(this);return this}});L.markers.push(m);return m};return L;
}
if(typeof module!=='undefined')module.exports={createLeaflet};
