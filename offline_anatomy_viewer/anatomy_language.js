"use strict";
// Source objects remain unchanged: localization is a presentation projection.
window.AnatomyLanguage = Object.freeze({
  locale(mode) { return mode === "en-vi" ? "vi" : mode; },
  fieldStatus(row, field) {
    return row && Object.hasOwn(row, "field_status") ? row.field_status?.[field] : row?.status;
  },
  resolve(pack, collection, key, field, original) {
    const row = pack?.[collection]?.[String(key)];
    const value = row?.translation?.[field];
    const status = this.fieldStatus(row, field);
    const translated = status === "reviewed" && row.source && Object.hasOwn(row.source, field) && row.source[field] === original
      && typeof value === "string" && Boolean(value.trim());
    return { text: translated ? value : original, translated: Boolean(translated) };
  },
  field(pack, collection, key, field, original) {
    return this.resolve(pack, collection, key, field, original).text;
  },
  lines(mode, original, value) {
    if (mode === "en-vi") return [
      { text: original || "", lang: "en", missing: false },
      { text: value.translated ? value.text : "Chưa có bản dịch", lang: "vi", missing: !value.translated },
    ];
    return [{ text: value.text || "", lang: value.translated ? this.locale(mode) : "en", missing: false }];
  },
  labelFontSize(label, neighbours) {
    // Fit two lines inside the source column's vertical slot, including wrapped
    // fragments. Source anchors and leader geometry must not be moved to fit text.
    let size = 16;
    for (const other of neighbours) {
      const gap = Math.abs(Number(other.y) - Number(label.y));
      if (other !== label && gap > 0 && Math.abs(Number(other.x) - Number(label.x)) < 160
          && other.text_align === label.text_align) size = Math.min(size, gap / 3);
    }
    return Math.max(6, size);
  },
  labelKey(series, variant, sliceId, canvas, index) {
    return JSON.stringify([series, variant, sliceId, canvas].map((value) => String(value ?? "")).concat(Number(index)));
  },
  targetKey(series, variant, sliceId, target) {
    return JSON.stringify(["target", ...[series, variant, sliceId].map((value) => String(value ?? "")),
      String(target.point_id ?? ""), Number(target.x), Number(target.y)]);
  },
  searchText(value) {
    return String(value || "").toLowerCase().replace(/đ/g, "d")
      .normalize("NFD").replace(/\p{M}/gu, "")
      .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  },
});
