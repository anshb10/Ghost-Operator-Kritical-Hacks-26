/* Light / dark theme toggle. Default follows the OS; once the user clicks, their choice is remembered. */
(function () {
  var root = document.documentElement, KEY = "go-theme";
  function stored() { try { var v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : null; } catch (e) { return null; } }
  function apply(t) { root.setAttribute("data-theme", t); }
  var btn = document.getElementById("theme-toggle");
  if (btn) btn.addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  });
  // follow OS changes until the user picks explicitly
  if (window.matchMedia) {
    var mq = matchMedia("(prefers-color-scheme: dark)");
    var onChange = function (e) { if (!stored()) apply(e.matches ? "dark" : "light"); };
    mq.addEventListener ? mq.addEventListener("change", onChange) : mq.addListener(onChange);
  }
})();
