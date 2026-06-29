// Service worker network-first: sempre busca a versão do servidor; cai para cache só offline.
// Evita que um terminal fique preso em uma versão antiga do sistema.
const CACHE = "estrela-v2";
const ESTATICOS = [
  "/static/css/output.css",
  "/static/js/htmx.min.js",
  "/static/js/alpine.min.js",
  "/static/js/ui.js",
  "/static/fonts/Inter-Regular.woff2",
  "/static/fonts/Inter-Medium.woff2",
  "/static/fonts/Inter-SemiBold.woff2",
  "/static/fonts/Inter-Bold.woff2",
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(ESTATICOS).catch(() => {})));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((nomes) =>
      Promise.all(nomes.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  // Só lida com GET; o resto vai direto para a rede.
  if (req.method !== "GET") return;

  event.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp && resp.ok && req.url.startsWith(self.location.origin)) {
          const copia = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copia)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(req).then((c) => c || Response.error()))
  );
});
