(function () {
  const mermaidSource =
    "https://unpkg.com/mermaid@11.16.0/dist/mermaid.min.js";
  let initialized = false;
  let mermaidPromise;

  const loadMermaid = () => {
    if (window.mermaid) {
      return Promise.resolve(window.mermaid);
    }
    if (mermaidPromise) {
      return mermaidPromise;
    }

    mermaidPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = mermaidSource;
      script.async = true;
      script.addEventListener("load", () => resolve(window.mermaid));
      script.addEventListener("error", () => {
        mermaidPromise = undefined;
        reject(new Error("Unable to load Mermaid"));
      });
      document.head.appendChild(script);
    });
    return mermaidPromise;
  };

  const renderMermaid = () => {
    if (!document.querySelector(".mermaid:not([data-processed])")) {
      return;
    }

    loadMermaid()
      .then((mermaid) => {
        if (!mermaid) {
          throw new Error("Mermaid loaded without exposing its API");
        }
        if (!initialized) {
          mermaid.initialize({
            startOnLoad: false,
            securityLevel: "strict",
          });
          initialized = true;
        }
        return mermaid.run({
          querySelector: ".mermaid:not([data-processed])",
        });
      })
      .catch((error) => console.error("Unable to render Mermaid diagram", error));
  };

  if (typeof document$ !== "undefined") {
    document$.subscribe(renderMermaid);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderMermaid);
  } else {
    renderMermaid();
  }
})();
