"use strict";

(() => {
  const password = document.getElementById("login-password");
  const toggle = document.getElementById("togglePasswordBtn");
  if (!password || !toggle) return;

  toggle.addEventListener("click", () => {
    const visible = password.type === "text";
    password.type = visible ? "password" : "text";
    toggle.classList.toggle("is-visible", !visible);
    toggle.setAttribute("aria-pressed", String(!visible));
    toggle.setAttribute("aria-label", visible ? "Hiện mật khẩu" : "Ẩn mật khẩu");
  });
})();
