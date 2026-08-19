// Service worker do Painel de Investimentos.
// Estratégia: o index.html sempre tenta buscar a versão mais nova na rede
// (é ele que muda todo dia); os arquivos estáticos (manifest, ícones) usam
// cache-first, já que praticamente nunca mudam. Sem internet, tudo cai no
// que ficou salvo na última visita.

const CACHE = "painel-cripto-v3";
const ESTATICOS = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icons/icon-192-v3.png",
  "./icons/icon-512-v3.png",
  "./icons/icon-512-maskable-v3.png",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ESTATICOS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys().then((chaves) =>
      Promise.all(chaves.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (evento) => {
  const url = new URL(evento.request.url);
  const ehPainel = evento.request.mode === "navigate" || url.pathname.endsWith("index.html");

  if (ehPainel) {
    // network-first: sempre tenta a cotação mais recente; se falhar, usa o cache.
    evento.respondWith(
      fetch(evento.request)
        .then((resposta) => {
          const copia = resposta.clone();
          caches.open(CACHE).then((cache) => cache.put("./index.html", copia));
          return resposta;
        })
        .catch(() => caches.match("./index.html"))
    );
    return;
  }

  // demais arquivos: cache-first, com atualização silenciosa em segundo plano.
  evento.respondWith(
    caches.match(evento.request).then((emCache) => {
      const buscaRede = fetch(evento.request)
        .then((resposta) => {
          const copia = resposta.clone();
          caches.open(CACHE).then((cache) => cache.put(evento.request, copia));
          return resposta;
        })
        .catch(() => emCache);
      return emCache || buscaRede;
    })
  );
});
