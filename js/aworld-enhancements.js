(function () {
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  const throttle = (fn, wait = 50) => {
    let last = 0;
    return (...args) => {
      const now = Date.now();
      if (now - last >= wait) {
        last = now;
        fn(...args);
      }
    };
  };

  const createProgressBar = () => {
    const bar = document.createElement("div");
    bar.className = "aw-scroll-progress";
    document.body.appendChild(bar);
    return bar;
  };

  const bindScrollProgress = (bar) => {
    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = document.documentElement;
      const percent = clamp(scrollTop / (scrollHeight - clientHeight || 1), 0, 1);
      bar.style.transform = `scaleX(${percent})`;

      const header = document.querySelector(".md-header");
      if (header) {
        header.classList.toggle("is-compact", scrollTop > 32);
      }
    };

    onScroll();
    document.addEventListener("scroll", throttle(onScroll, 20), { passive: true });
  };

  const upgradeExternalLinks = () => {
    const links = document.querySelectorAll(".md-typeset a[href^='http']");
    const currentHost = window.location.host;

    links.forEach((link) => {
      const isSameHost = link.host === currentHost;
      if (!isSameHost) {
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
      }
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    const bar = createProgressBar();
    bindScrollProgress(bar);
    upgradeExternalLinks();
  });
})();

