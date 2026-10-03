// static/attachments.js
// Estado y logica de adjuntos de archivos (documentos como texto).
// Se mezcla con chatState() en el registro de voiceApp (ver index.html).
function attachmentState() {
  return {
    pendingAttachments: [],

    async attachFiles(event) {
      const files = Array.from(event.target.files || []);
      event.target.value = "";
      if (files.length === 0) return;
      if (this.pendingAttachments.length + files.length > 5) {
        this.statusText = "Maximo 5 adjuntos por mensaje";
        setTimeout(() => (this.statusText = "Listo"), 3000);
        return;
      }
      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file);
        this.statusText = "Subiendo " + file.name + "...";
        try {
          const res = await fetch("/api/upload", { method: "POST", body: formData });
          const data = await res.json();
          if (!res.ok || data.error) {
            this.statusText = "Error al subir: " + (data.error || res.status);
          } else {
            this.pendingAttachments.push({
              file_id: data.file_id,
              name: data.name,
              chars: data.chars,
            });
            this.statusText = data.warning || "Listo";
          }
        } catch (e) {
          this.statusText = "Error de red al subir archivo";
        }
        setTimeout(() => (this.statusText = "Listo"), 3000);
      }
    },

    removePendingAttachment(index) {
      this.pendingAttachments.splice(index, 1);
    },

    buildMarker(att) {
      const name = String(att.name || "").replace(/[\[\]|]/g, "_").slice(0, 80);
      return `[adjunto:${att.file_id}|${name}]`;
    },

    parseMarkers(text) {
      // Extrae [adjunto:<file_id>|<nombre>] de un mensaje de usuario y devuelve
      // { clean_text, attachments: [{file_id, name}] }.
      const attachments = [];
      const clean = String(text || "").replace(
        /\[adjunto:([0-9a-f]{12})\|([^\]]*)\]/g,
        (m, id, name) => {
          attachments.push({ file_id: id, name: name });
          return "";
        }
      );
      return { clean_text: clean.replace(/\n{3,}/g, "\n\n").trim(), attachments };
    },
  };
}