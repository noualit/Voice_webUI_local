// static/metricsState.js
// Estado y logica de metricas de generacion (tiempo, tokens, velocidad).
// Se mezcla con chatState() en el registro de voiceApp (ver index.html).
function metricsState() {
  return {
    // Metricas de generacion (estilo llama-server): velocidad y acumulados.
    // tokens/tokens_per_sec usan los valores REALES que envia el backend en
    // el bloque "usage" del stream (stream_options.include_usage), no estimados.
    metrics: { elapsed_ms: 0, chars: 0, tokens: 0, prompt_tokens: 0, tokens_est: 0, tokens_per_sec: 0 },

    resetMetrics() {
      this.metrics = { elapsed_ms: 0, chars: 0, tokens: 0, prompt_tokens: 0, tokens_est: 0, tokens_per_sec: 0 };
    },

    // Actualizar metricas locales (estimadas, ~1 token por 4 chars).
    updateDelta(chars) {
      this.metrics.chars += chars;
      this.metrics.tokens_est = Math.round(this.metrics.chars / 4);
      this.metrics.tokens_per_sec = this.metrics.elapsed_ms > 0
        ? (this.metrics.tokens_est / (this.metrics.elapsed_ms / 1000))
        : 0;
    },

    // Evento "metrics" del stream: el backend envia chars acumulados y tiempo.
    applyMetricsEvent(evt) {
      this.metrics.elapsed_ms = evt.elapsed_ms;
      this.metrics.chars = evt.chars;
      this.metrics.tokens_est = Math.round(evt.chars / 4);
      this.metrics.tokens_per_sec = evt.elapsed_ms > 0
        ? (this.metrics.tokens_est / (this.metrics.elapsed_ms / 1000))
        : 0;
    },

    // Evento "done": capturar tokens REALES del bloque "usage" que envia
    // el backend (suma de todas las iteraciones) y calcular tokens/segundo
    // reales con el tiempo transcurrido.
    applyDoneEvent(evt) {
      this.metrics.tokens = evt.tokens || this.metrics.tokens;
      this.metrics.prompt_tokens = evt.prompt_tokens || this.metrics.prompt_tokens;
      if (evt.elapsed_ms) this.metrics.elapsed_ms = evt.elapsed_ms;
      const elapSec = this.metrics.elapsed_ms / 1000;
      this.metrics.tokens_per_sec = elapSec > 0
        ? (this.metrics.tokens / elapSec)
        : 0;
    },
  };
}
