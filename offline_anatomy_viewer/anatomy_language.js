"use strict";
// Source objects remain unchanged: localization is a presentation projection.
window.AnatomyLanguage = Object.freeze({
  field(pack, collection, key, field, original) {
    const row = pack?.[collection]?.[String(key)];
    const value = row?.translation?.[field];
    return row?.status === "reviewed" && row.source && Object.hasOwn(row.source, field) && row.source[field] === original
      && typeof value === "string" && value.trim() ? value : original;
  },
  labelKey(series, variant, sliceId, canvas, index) {
    return JSON.stringify([String(series), String(variant), String(sliceId), String(canvas), Number(index)]);
  },
  targetKey(series, variant, sliceId, target) {
    return JSON.stringify(["target", String(series), String(variant), String(sliceId),
      String(target.point_id ?? ""), Number(target.x), Number(target.y)]);
  },
  searchText(value) {
    return String(value || "").toLocaleLowerCase("vi").replace(/đ/g, "d")
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/\[[^\]]+]/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
  },
});
