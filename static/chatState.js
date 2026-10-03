// chatState.js - Estado y logica principal del chat
// Se carga ANTES de Alpine para permitir registro en evento alpine:init

function chatState() {
  return {
    sidebarOpen: window.innerWidth > 768,
    showConfig: false,
    user: null,
    loginName: "",
    conversations: [],
    currentConvId: null,
    messages: [],
   draft: "",
    loading: false,
    recording: false,
    memoryOnline: false,
    model: "",
    statusText: "Listo",
    mediaRecorder: null,
    audioChunks: [],
    streamCancelled: false,
    abortController: null,
      // Solicitud de aprobacion de tool peligrosa pendiente (p.ej. run_command).
      // El backend espera la decision; se responde via respondApproval().
      approval: null,

settings: {
       system_prompt: "",
       llama_url: "",
       model: "",
       whisper_model: "",
     },
    mcpServers: [],
    newMcp: { name: "", transport: "stdio", command: "", args: "", url: "", headers: "", timeout: 30 },

    async init() {
      await this.checkMe();
      if (!this.user) return;
      this.loadConfig();
      this.loadConversations();
      this.loadMcpStatus();
      this.checkHealth();
    },

    async checkMe() {
      try {
        const res = await fetch("/api/me");
        const data = await res.json();
        this.user = data.user || null;
      } catch (e) {
        this.user = null;
      }
    },

    async login() {
      const name = this.loginName.trim();
      if (!name) return;
      try {
        const res = await fetch("/api/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name }),
        });
        const data = await res.json();
        if (data.user) {
          this.user = data.user;
          this.loginName = "";
          this.loadConfig();
          this.loadConversations();
          this.loadMcpStatus();
          this.checkHealth();
        }
      } catch (e) {
        console.error("Error en login", e);
      }
    },

    async logout() {
      try {
        await fetch("/api/logout", { method: "POST" });
      } catch (e) {
        console.error("Error en logout", e);
      }
      this.user = null;
      this.conversations = [];
      this.messages = [];
      this.currentConvId = null;
    },

    handleApprovalRequest(evt) {
      // Muestra el modal de aprobacion con los detalles de la tool peligrosa.
      this.approval = {
        approval_id: evt.approval_id,
        tool: evt.tool,
        arguments: evt.arguments || {},
      };
    },

    async respondApproval(approved) {
      // Envia la decision del usuario al backend, que desbloquea el stream.
      const ap = this.approval;
      if (!ap) return;
      try {
        await fetch("/api/chat/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approval_id: ap.approval_id, approved }),
        });
        } catch (e) {
          console.error("Error respondiendo aprobacion", e);
        }
        this.approval = null;
      },

      handleApprovalExpired(evt) {
        // Bug 2026-09-18 (reportado por el usuario: "apruebo y no ejecuta, el
        // modal pide permiso de nuevo"): el backend emite approval_expired
        // cuando APPROVAL_TIMEOUT caduca SIN que el usuario decidiera. Hoy
        // este evento caia al cierre silencioso del stream y el modal quedaba
        // ABIERTO pidiendo permiso (ademas el backend esta reintentando la
        // tool, lo que reabria el modal en loop). Al recibirlo cerramos el
        // modal y avisamos que el permiso caduco para que el usuario sepa que
        // debe volver a pedir la accion (no quedo colgado esperando).
        this.approval = null;
        this.statusText = `Permiso para "${evt.tool || ""}" caducado (no aprobada). Vuelve a pedir la accion.`;
        const botIdx = this.messages.length - 1;
        this.messages.push({
          role: "assistant",
          content: `No se pudo ejecutar "${evt.tool || ""}" porque el permiso caduco mientras esperabas. Si aun la necesitas, pidelo de nuevo.`,
          _expired: true,
        });
        if (typeof botIdx === "number" && botIdx >= 0 && this.messages[botIdx]?.role === "assistant") {
          this.messages[botIdx]._expired = true;
        }
      },


    async loadConfig() {
      try {
        const res = await fetch("/api/config");
        const data = await res.json();
        this.settings.system_prompt = data.system_prompt || "";
        this.settings.llama_url = data.llama_url || "";
        this.settings.model = data.model || "";
        this.settings.whisper_model = data.whisper_model || "";
        this.model = data.model || "";
      } catch (e) {
        console.error("Error cargando config", e);
      }
    },

    async saveConfig() {
      try {
        await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
body: JSON.stringify({
             system_prompt: this.settings.system_prompt,
             llama_url: this.settings.llama_url,
             model: this.settings.model,
           }),
        });
        this.model = this.settings.model;
        this.statusText = "Config guardada";
        setTimeout(() => (this.statusText = "Listo"), 2000);
      } catch (e) {
        console.error("Error guardando config", e);
        this.statusText = "Error al guardar";
      }
    },

   async loadConversations() {
       try {
         const res = await fetch("/api/conversations");
         const data = await res.json();
         let convs = data.conversations || [];

         // Fetch meta (e.g. renamed titles) for all conversations in one call
         if (convs.length > 0) {
           try {
             const ids = convs.map(c => c.id).join(",");
             const metaRes = await fetch(`/api/conversations/meta?ids=${encodeURIComponent(ids)}`);
             if (metaRes.ok) {
               const meta = await metaRes.json();
               for (const c of convs) {
                 const metas = meta[c.id] || [];
                 for (const m of metas) {
                   const text = (m.text || "").trim();
                   if (text.startsWith("conversation_name=")) {
                     c.name = text.slice(18); // strip prefix
                     break;
                   }
                 }
               }
             }
           } catch (e) {
             console.error("Error cargando metadatos de conversaciones", e);
           }
         }

         this.conversations = convs;
       } catch (e) {
         console.error("Error cargando conversaciones", e);
       }
     },

    async loadMcpStatus() {
      try {
        const res = await fetch("/api/mcp-status");
        const data = await res.json();
        this.mcpServers = data.servers || [];
      } catch (e) {
        console.error("Error cargando MCP", e);
      }
    },

    async checkHealth() {
      try {
        const res = await fetch("/api/health");
        const data = await res.json();
        this.memoryOnline = data.memory && data.memory.status === "ok";
      } catch (e) {
        this.memoryOnline = false;
      }
    },

    async newChat() {
      try {
        const res = await fetch("/api/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "Nueva conversacion" }),
        });
        const data = await res.json();
        this.currentConvId = data.conversation_id;
        this.messages = [];
        this.loadConversations();
      } catch (e) {
        console.error("Error creando conversacion", e);
      }
    },

    async openConversation(id) {
      this.currentConvId = id;
      try {
        const res = await fetch(`/api/conversations/${id}`);
        const data = await res.json();
        const mems = data.memories || [];
        this.messages = mems.map((m) => {
          const role = m.role === "user" ? "user" : m.role === "note" ? "note" : "assistant";
          if (role === "user") {
            const parsed = this.parseMarkers(m.text || "");
            return { role, content: parsed.clean_text, attachments: parsed.attachments };
          }
          return { role, content: m.text || "" };
        });
        this.scrollToBottom();
      } catch (e) {
        console.error("Error abriendo conversacion", e);
      }
    },

    exportConversation() {
      // Abre la conversacion como HTML autocontenido en una pestaña nueva.
      // La pagina exportada dispara window.print() al cargar, asi el usuario
      // puede guardarla como PDF desde el dialogo de impresion del navegador.
      if (!this.currentConvId) return;
      window.open(`/api/conversations/${this.currentConvId}/export`, "_blank");
    },

    async deleteConversation(id) {
       if (!confirm("Eliminar esta conversacion?")) return;
       try {
         const res = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
         const data = await res.json();
         if (data.deleted) {
           if (this.currentConvId === id) {
             this.currentConvId = null;
             this.messages = [];
           }
           this.loadConversations();
         }
       } catch (e) {
         console.error("Error eliminando conversacion", e);
       }
     },

    async editConversationTitle(id, currentName) {
       const newName = prompt("Cambiar titulo de conversacion", currentName);
       if (!newName || newName.trim() === (currentName || "").trim()) return;
       await this.renameConversation(id, newName.trim());
     },

    async renameConversation(id, newName) {
       const name = (newName || "").trim();
       if (!name) return;
       try {
         const res = await fetch(`/api/conversations/${id}/rename`, {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({ name }),
         });
         if (res.ok) {
           await this.loadConversations();
         }
       } catch (e) {
         console.error("Error renombrando conversacion", e);
       }
     },

async sendText() {
       const cleanDraft = this.draft.trim();
       const attachments = [...(this.pendingAttachments || [])];
       const markers = attachments.map((a) => this.buildMarker(a)).join("\n");
       const text = cleanDraft + (markers ? "\n" + markers : "");
       if (!text.trim()) return;
       this.draft = "";
       this.pendingAttachments = [];
       this.messages.push({ role: "user", content: cleanDraft, attachments });
       this.loading = true;
       this.streamCancelled = false;
       this.abortController = new AbortController();
       this.resetMetrics();
       this.scrollToBottom();

       try {
         const res = await fetch("/api/chat", {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({
             text,
             conversation_id: this.currentConvId,
           }),
           signal: this.abortController.signal,
         });

         // El mensaje del asistente NO se crea al inicio: las tools llegan
          // primero (el modelo ejecuta tools antes de generar la respuesta), asi
          // que se van anadiendo al final. Cuando llega el primer delta, se crea
          // el mensaje DEBAJO de las tools en el array.
          const processor = createStreamProcessor({
             messages: this.messages,
             updateDelta: (n) => this.updateDelta(n),
             applyMetricsEvent: (evt) => this.applyMetricsEvent(evt),
             scrollToBottom: () => this.scrollToBottom(),
             onApprovalRequest: (evt) => this.handleApprovalRequest(evt),
             onCancelled: (botIdx) => {
               this.streamCancelled = true;
               if (botIdx !== null) this.statusText = "Cancelado";
             },
             onDone: (evt) => this.applyDoneEvent(evt),
             isCancelled: () => this.streamCancelled,
           });

          await processor.readStream(res.body, null, true);
        } catch (e) {
          if (e.name !== "AbortError") {
            this.messages.push({ role: "error", content: "Error de connexion: " + e.message });
          }
        } finally {
          this.loading = false;
          this.statusText = "Listo";
          this.approval = null;
          this.scrollToBottom();
          this.loadConversations();
        }
      },

    async stopChat() {
        // Aborta el fetch en curso para romper el loop de streaming inmediatamente.
        if (this.abortController) {
          this.abortController.abort();
          this.abortController = null;
        }
        // Tambien marca el flag por si queda algun evento pendiente.
        this.streamCancelled = true;
        // Notifica al backend que cancele la generacion del LLM.
        if (!this.currentConvId) return;
        try {
          await fetch("/api/chat/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: this.currentConvId }),
          });
        } catch (e) {
          console.error("Error cancelando respuesta", e);
        }
      },

    async toggleRec() {
      if (this.recording) {
        await this.stopRec();
      } else {
        await this.startRec();
      }
    },

    async startRec() {
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        this.statusText = "Grabacion no soportada";
        return;
      }
      if (this.recording) return;

      let stopResolve = null;
      const stopPromise = new Promise((resolve) => { stopResolve = resolve; });

      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.mediaRecorder = new MediaRecorder(stream);
        this.audioChunks = [];

        // ondataavailable se dispara periodicamente mientras graba Y al detenerse.
        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) this.audioChunks.push(e.data);
        };

        // onstop: se dispara cuando el recorder termina completamente.
        // Garantiza que todos los chunks (incluido el ultimo) ya fueron
        // entregados via ondataavailable.
        this.mediaRecorder.onstop = () => {
          if (stopResolve) stopResolve();
          this.mediaRecorder = null;
          this.recording = false;
          this.statusText = "Procesando...";
        };

        this.mediaRecorder.start();
        this.recording = true;
        this.statusText = "Grabando...";

        // Bloquear hasta que el recorder se detenga (via stopRec o onstop).
        await stopPromise;

        await this.sendAudio();
      } catch (e) {
        console.error("Error al iniciar grabacion", e);
        this.statusText = "Error de microfono";
        this.recording = false;
        this.mediaRecorder = null;
      }
    },

    async stopRec() {
      if (this.mediaRecorder && this.mediaRecorder.state === "recording") {
        // Detener el grabador. onstop se encargara de limpiar tracks,
        // actualizar estado y llamar sendAudio().
        this.mediaRecorder.stop();
      } else {
        console.warn("stopRec: mediaRecorder no disponible o no grabando");
      }
    },

    async sendAudio() {
      const blob = new Blob(this.audioChunks, { type: "audio/webm" });
      const formData = new FormData();
      formData.append("audio", blob, "rec.webm");
      this.statusText = "Procesando...";
      try {
        const res = await fetch("/api/transcribe", { method: "POST", body: formData });
        const data = await res.json();
        if (data.transcription) {
          this.draft = data.transcription;
          await this.sendText();
        } else {
          this.statusText = data.error || "No se detecto voz";
          setTimeout(() => (this.statusText = "Listo"), 2000);
        }
      } catch (e) {
        this.statusText = "Error de transcripcion";
        setTimeout(() => (this.statusText = "Listo"), 2000);
      }
    },

    async addMCP() {
      let args = [];
      try {
        args = JSON.parse(this.newMcp.args || "[]");
      } catch (e) {
        args = [];
      }
      let headers = {};
      try {
        headers = JSON.parse(this.newMcp.headers || "{}");
      } catch (e) {
        headers = {};
      }
      const payload = {
        name: this.newMcp.name.trim(),
        transport: this.newMcp.transport,
      };
      if (this.newMcp.transport === "http" || this.newMcp.transport === "sse") {
        payload.url = this.newMcp.url.trim();
        payload.headers = headers;
        payload.timeout = parseInt(this.newMcp.timeout, 10) || 30;
      } else {
        payload.command = this.newMcp.command.trim();
        payload.args = args;
      }
      if (!payload.name || (payload.transport !== "http" && payload.transport !== "sse" && !payload.command) || ((payload.transport === "http" || payload.transport === "sse") && !payload.url)) {
        this.statusText = "Faltan campos (name + command o url)";
        setTimeout(() => (this.statusText = "Listo"), 3000);
        return;
      }
      try {
        const res = await fetch("/api/mcp/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok || data.error) {
          this.statusText = "Error al agregar MCP: " + (data.error || res.status);
        } else {
          this.statusText = "MCP agregado";
        }
        setTimeout(() => (this.statusText = "Listo"), 3000);
        this.newMcp = { name: "", transport: "stdio", command: "", args: "", url: "", headers: "", timeout: 30 };
        this.loadMcpStatus();
      } catch (e) {
        console.error("Error agregando MCP", e);
        this.statusText = "Error de red al agregar MCP";
        setTimeout(() => (this.statusText = "Listo"), 3000);
      }
    },

    async removeMCP(name) {
      try {
        await fetch("/api/mcp/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name }),
        });
        this.loadMcpStatus();
      } catch (e) {
        console.error("Error eliminando MCP", e);
      }
    },

   hasAssistantAfter(userIdx) {
        // True si existe un mensaje del asistente despues de la pregunta:
        // es la respuesta que el boton de esta pregunta puede regenerar.
        for (let i = userIdx + 1; i < this.messages.length; i++) {
            if (this.messages[i].role === "assistant") return true;
        }
        return false;
    },

   async resendMessage(userIdx) {
        // Boton reload junto a la pregunta del usuario: regenera la respuesta
        // del asistente a ESA pregunta (la siguiente en el historial).
        // El mensaje del usuario ya esta en memoria (resend=true) y NO se
        // guarda de nuevo; solo se guarda la nueva respuesta del asistente.
        let botIdx = null;
        for (let i = userIdx + 1; i < this.messages.length; i++) {
            if (this.messages[i].role === "assistant") {
                botIdx = i;
                break;
            }
        }
        if (botIdx === null) {
            this.statusText = "No hay respuesta que regenerar";
            setTimeout(() => (this.statusText = "Listo"), 2000);
            return;
        }

        const userMsg = this.messages[userIdx];

        const atts = userMsg.attachments || [];
        const markers = atts.map((a) => this.buildMarker(a)).join("\n");
        const text = (userMsg.content || "").trim() + (markers ? "\n" + markers : "");

        this.loading = true;
        this.streamCancelled = false;
        this.abortController = new AbortController();
        this.resetMetrics();
        // Limpiar el contenido del mensaje asistente antes de regenerar.
        this.messages[botIdx] = { ...this.messages[botIdx], content: "" };
        this.scrollToBottom();

        try {
          const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text,
              conversation_id: this.currentConvId,
              resend: true,
            }),
            signal: this.abortController.signal,
          });

          // Reutilizar el mensaje existente (botIdx): NO se crea nuevo msg.
          const processor = createStreamProcessor({
            messages: this.messages,
            updateDelta: (n) => this.updateDelta(n),
            applyMetricsEvent: (evt) => this.applyMetricsEvent(evt),
            scrollToBottom: () => this.scrollToBottom(),
            onApprovalRequest: (evt) => this.handleApprovalRequest(evt),
            onCancelled: (idx) => {
              this.streamCancelled = true;
              if (idx !== null) this.statusText = "Cancelado";
            },
            onDone: (evt) => this.applyDoneEvent(evt),
            isCancelled: () => this.streamCancelled,
          });

          await processor.readStream(res.body, botIdx, false);
        } catch (e) {
          if (e.name !== "AbortError") {
            this.messages.push({ role: "error", content: "Error de connexion: " + e.message });
          }
        } finally {
          this.loading = false;
          this.statusText = "Listo";
          this.approval = null;
          this.scrollToBottom();
          this.loadConversations();
        }
      },

     scrollToBottom() {
      setTimeout(() => {
        const el = document.getElementById("chatArea");
        if (el) el.scrollTop = el.scrollHeight;
      }, 50);
    },
  };
}
