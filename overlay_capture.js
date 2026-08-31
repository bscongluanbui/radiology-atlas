/* Native, authenticated renderer adapter. No URL guessing, colour segmentation,
   credential access, filter changes, or writes to the online viewer. */
async (wanted) => {
  const fail = (code) => { throw new Error(code); };
  const canvas = document.querySelector('canvas[data-name="image-canvas"]');
  if (!canvas) fail('OVERLAY_IMAGE_CANVAS_MISSING');
  const candidates = new Set();
  const bridge = window.__offlineOverlayRuntime;
  if (bridge?.version === 1) {
    for (const app of bridge.applicationsForCanvas(canvas)) candidates.add(app);
  }
  // Compatibility with development renderers; production discovery uses the
  // pre-navigation Pinia registration observer, not Vue debug DOM properties.
  for (let element = canvas; element; element = element.parentElement) {
    for (let component = element.__vueParentComponent; component; component = component.parent) {
      const stores = component.appContext?.config?.globalProperties?.$pinia?._s;
      if (stores?.values) for (const store of stores.values()) {
        const application = store.application?.value || store.application;
        if (application?.components?.imageCanvas?.getCanvas?.() === canvas) candidates.add(application);
      }
    }
  }
  if (candidates.size !== 1) {
    const diagnostics = bridge?.diagnostics?.() || {version: 0, stores_observed: 0};
    const code = candidates.size ? 'OVERLAY_NATIVE_ADAPTER_AMBIGUOUS' : 'OVERLAY_NATIVE_ADAPTER_UNAVAILABLE';
    fail(`${code}: candidates=${candidates.size}; bridge=${diagnostics.version}; stores=${diagnostics.stores_observed}`);
  }
  const app = [...candidates][0];
  const required = {
    image: ['getImage','getImageSettings','getCurrentId','canBeRendered','getLastImageRendered','getFromCache'],
    slice: ['getCurrent'], series: ['findById'], renderingContext: ['get'], overlay: ['findById','existOverlay'],
  };
  for (const [name, methods] of Object.entries(required)) {
    for (const method of methods) {
      if (typeof app.services?.[name]?.[method] !== 'function') fail(`OVERLAY_NATIVE_INTERFACE_UNSUPPORTED:${name}.${method}`);
    }
  }
  const service = app.services.image;
  const nativeSlice = app.services.slice.getCurrent();
  const series = app.services.series.findById(nativeSlice.series_id);
  const ctx = canvas.getContext('2d');
  const rendering = app.services.renderingContext.get();
  const matrix = () => { const t = ctx.getTransform(); return [t.a,t.b,t.c,t.d,t.e,t.f]; };
  const originalMatrix = matrix();
  const currentCode = () => app.getCurrentContrastCode?.() ?? null;
  const contrastCode = currentCode();
  let changedDuringCapture = false;
  const check = () => {
    if (changedDuringCapture) fail('SLICE_CHANGED_DURING_CAPTURE:overlay_observer');
    const active = document.querySelector('.indicator-bar .slice.indicator');
    const row = app.services.slice.getCurrent();
    if (String(row.id) !== String(wanted.slice_id) || String(active?.dataset.id) !== String(wanted.slice_id)
        || Number(active?.dataset.index) !== Number(wanted.global_index)
        || String(row.series_id) !== String(wanted.series_id)
        || Number(row.sort_order) !== Number(wanted.sort_order)) fail('SLICE_CHANGED_DURING_CAPTURE:overlay');
    if (currentCode() !== contrastCode || JSON.stringify(matrix()) !== JSON.stringify(originalMatrix))
      fail('OVERLAY_VIEW_CHANGED');
    const settings = service.getImageSettings(service.getCurrentId());
    if (!settings || String(settings.slice_id) !== String(wanted.slice_id) || settings.type !== 'slice'
        || !service.canBeRendered(service.getCurrentId())
        || service.getLastImageRendered() !== service.getFromCache(service.getCurrentId()))
      fail('OVERLAY_BASE_IMAGE_NOT_CURRENT');
  };
  check();
  const digest = async (data) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))]
    .map(b => b.toString(16).padStart(2, '0')).join('');
  const pngBytes = (uri) => Uint8Array.from(atob(uri.split(',')[1]), c => c.charCodeAt(0));
  const baseHash = await digest(pngBytes(canvas.toDataURL('image/png')));
  if (baseHash !== wanted.base_image_sha256 || canvas.width !== wanted.canvas_width || canvas.height !== wanted.canvas_height)
    fail('OVERLAY_BASE_ALIGNMENT_MISMATCH');
  const {IMAGE_OFFSET_X:x, IMAGE_OFFSET_Y:y, MULTIPLIER:scale} = rendering;
  if (![x,y,scale,series.slices_width,series.slices_height,...originalMatrix].every(Number.isFinite)
      || scale <= 0 || series.slices_width <= 0) fail('OVERLAY_TRANSFORM_INVALID');
  // Same placement as the source renderer's drawImageOverlays; in image-canvas pixels.
  const output = [];
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (!m.target.matches?.('.slice[data-id]')) continue;
      if (m.attributeName !== 'class' || ((m.oldValue || '').split(/\s+/).includes('indicator') !== m.target.classList.contains('indicator')))
        changedDuringCapture = true;
    }
  });
  observer.observe(document.querySelector('.indicator-bar'), {subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['class','data-id','data-index']});
  try {
  for (const layer of wanted.layers) {
    try {
      check();
      const descriptor = app.services.overlay.findById(layer);
      if (!descriptor || !app.services.overlay.existOverlay(layer, nativeSlice)) fail('OVERLAY_NATIVE_RANGE_MISMATCH');
      const request = {slice_id: nativeSlice.id, format: 'png', type: 'overlay', code: layer};
      // The native image service keys overlay requests by all option values.
      // A fresh request key retries a cached null/error, without changing the
      // layer, slice, resource URL, authentication, or normal server controls.
      if (wanted.retry_index > 0) request.capture_retry = `${Date.now()}-${wanted.retry_index}`;
      let timer;
      const result = await Promise.race([
        service.getImage(request),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('OVERLAY_LOAD_TIMEOUT')), wanted.timeout_ms); })
      ]).finally(() => clearTimeout(timer));
      check();
      const settings = service.getImageSettings(result?.image_id);
      if (!settings || String(settings.slice_id) !== String(wanted.slice_id) || settings.type !== 'overlay'
          || settings.code !== layer || Number(settings.sort_order) !== Number(wanted.sort_order)
          || !service.canBeRendered(result.image_id)
          || result.image !== service.getFromCache(result.image_id)) fail('OVERLAY_RESOURCE_IDENTITY_MISMATCH');
      const img = result.image;
      if (!img || !img.complete || !img.naturalWidth || !img.naturalHeight) fail('OVERLAY_IMAGE_MISSING');
      // Overlay-only data: never screenshot labels/text or an opaque base image.
      const scratch = document.createElement('canvas');
      scratch.width = img.naturalWidth; scratch.height = img.naturalHeight;
      const sc = scratch.getContext('2d', {willReadFrequently: true});
      sc.drawImage(img, 0, 0);
      const pixels = sc.getImageData(0,0,scratch.width,scratch.height).data;
      let transparent = 0, nonzero = 0;
      for (let i=3;i<pixels.length;i+=4) { if (pixels[i] === 0) transparent++; else nonzero++; }
      if (!transparent || !nonzero) fail(nonzero ? 'OVERLAY_OPAQUE_NOT_A_MASK' : 'OVERLAY_EMPTY_IMAGE');
      const unit = scale * series.slices_width / scratch.width;
      const [a,b,c,d,e,f] = originalMatrix;
      output.push({layer, status:'PASS', png: scratch.toDataURL('image/png'), width:scratch.width,
        height:scratch.height, transform:[a*unit,b*unit,c*unit,d*unit,a*x+c*y+e,b*x+d*y+f],
        opacity: Number(app.services.overlay.getDefaultImageOverlayOpacity?.() ?? 0.7),
        alpha:{transparent,nonzero}, proof:{method:'NATIVE_OVERLAY_IMAGE_SETTINGS_AND_BASE_HASH',
          slice_id:String(settings.slice_id),sort_order:Number(settings.sort_order),layer:settings.code,
          type:settings.type,base_image_sha256:baseHash,series_id:String(nativeSlice.series_id),contrast_code:contrastCode}});
    } catch (error) {
      if (/SLICE_CHANGED|OVERLAY_VIEW_CHANGED|OVERLAY_BASE_IMAGE_NOT_CURRENT/.test(String(error))) throw error;
      output.push({layer,status:'PARTIAL',error:String(error.message || error)});
    }
  }
  check();
  if (await digest(pngBytes(canvas.toDataURL('image/png'))) !== baseHash) fail('OVERLAY_VIEW_CHANGED');
  return {layers:output,base_image_sha256:baseHash,
    adapter:{method:bridge?.version === 1 ? 'PINIA_REGISTRATION_OBSERVER' : 'VUE_DEBUG_COMPATIBILITY',
      candidates:candidates.size, ...(bridge?.diagnostics?.() || {})}};
  } finally { observer.disconnect(); }
}
