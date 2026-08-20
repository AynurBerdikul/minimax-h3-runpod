import { app } from "../../scripts/app.js";

const EXT = "berdicool.runpod-h3-remote";
const API = {
    async json(path, options = {}) {
        const response = await fetch(path, options);
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    },
    get(path) { return this.json(path); },
    post(path, body = {}) {
        return this.json(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    },
};

function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (key === "style" && value && typeof value === "object") Object.assign(node.style, value);
        else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
        else if (key === "text") node.textContent = value;
        else if (key === "className") node.className = value;
        else if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    }
    for (const child of Array.isArray(children) ? children : [children]) {
        if (child === null || child === undefined) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function modal(title) {
    const overlay = el("div", { style: {
        position: "fixed", inset: "0", zIndex: "100000", background: "rgba(0,0,0,.66)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: "20px",
    }});
    const box = el("div", { style: {
        width: "min(720px, 94vw)", maxHeight: "88vh", overflow: "auto",
        background: "var(--comfy-menu-bg, #202020)", color: "var(--input-text, #eee)",
        border: "1px solid var(--border-color, #555)", borderRadius: "10px",
        boxShadow: "0 16px 60px rgba(0,0,0,.55)", padding: "18px",
        fontFamily: "sans-serif",
    }});
    const header = el("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", marginBottom: "14px" }}, [
        el("div", { text: title, style: { fontSize: "16px", fontWeight: "700" }}),
    ]);
    const close = el("button", { text: "×", style: buttonStyle(true), onclick: () => overlay.remove() });
    header.append(close);
    const body = el("div");
    box.append(header, body);
    overlay.append(box);
    overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.remove(); });
    document.body.append(overlay);
    return { overlay, box, body, close };
}

function buttonStyle(quiet = false) {
    return {
        border: "1px solid var(--border-color, #555)", borderRadius: "6px", cursor: "pointer",
        padding: quiet ? "4px 9px" : "7px 12px", fontSize: "12px",
        background: quiet ? "transparent" : "var(--comfy-input-bg, #333)", color: "inherit",
    };
}

function field(label, input) {
    return el("label", { style: { display: "grid", gap: "5px", marginBottom: "11px", fontSize: "12px" }}, [
        el("span", { text: label, style: { opacity: ".75" }}), input,
    ]);
}

function textInput(value = "", placeholder = "", type = "text") {
    return el("input", { type, value, placeholder, style: {
        width: "100%", boxSizing: "border-box", padding: "8px 9px", borderRadius: "6px",
        border: "1px solid var(--border-color, #555)", background: "var(--comfy-input-bg, #292929)", color: "inherit",
    }});
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

function mediaUrl(item) {
    const q = new URLSearchParams({
        filename: item.filename || "",
        subfolder: item.subfolder || "",
        type: item.type || "output",
    });
    return `/view?${q.toString()}`;
}

function mediaKind(item) {
    const type = String(item.content_type || "").toLowerCase();
    const name = String(item.filename || "").toLowerCase();
    if (type.startsWith("video/") || /\.(mp4|webm|mov|mkv|avi)$/i.test(name)) return "video";
    if (type.startsWith("audio/") || /\.(wav|mp3|ogg|flac|m4a|aac)$/i.test(name)) return "audio";
    if (type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp)$/i.test(name)) return "image";
    return "file";
}

function renderOutputs(body, outputs, jobId) {
    body.innerHTML = "";
    body.append(el("div", { text: `Completed · ${jobId}`, style: { marginBottom: "12px", color: "#66d98b", fontWeight: "700" }}));
    for (const item of outputs || []) {
        const url = mediaUrl(item);
        const kind = mediaKind(item);
        const card = el("div", { style: { borderTop: "1px solid var(--border-color, #444)", paddingTop: "12px", marginTop: "12px" }});
        if (kind === "video") {
            card.append(el("video", { src: url, controls: "controls", preload: "metadata", style: { width: "100%", maxHeight: "62vh", background: "#000", borderRadius: "8px" }}));
        } else if (kind === "audio") {
            card.append(el("audio", { src: url, controls: "controls", preload: "metadata", style: { width: "100%" }}));
        } else if (kind === "image") {
            card.append(el("img", { src: url, style: { maxWidth: "100%", maxHeight: "62vh", objectFit: "contain", borderRadius: "8px" }}));
        }
        const link = el("a", { href: url, target: "_blank", rel: "noopener", text: item.filename || "Open output", style: { display: "inline-block", marginTop: "8px", color: "#7fb8ff" }});
        card.append(link);
        body.append(card);
    }
}

async function showSettings() {
    const m = modal("RunPod H3 Settings");
    let cfg;
    try { cfg = await API.get("/runpod-h3/config"); }
    catch (error) { m.body.textContent = error.message; return; }

    const endpoint = textInput(cfg.endpoint_id || "");
    const apiKey = textInput("", cfg.runpod_api_key ? `Keep existing (${cfg.runpod_api_key})` : "RunPod API key", "password");
    const s3Endpoint = textInput(cfg.s3_endpoint || "");
    const s3Region = textInput(cfg.s3_region || "");
    const volume = textInput(cfg.volume_id || "");
    const access = textInput("", cfg.s3_access_key ? `Keep existing (${cfg.s3_access_key})` : "S3 access key", "password");
    const secret = textInput("", cfg.s3_secret_key ? `Keep existing (${cfg.s3_secret_key})` : "S3 secret key", "password");
    const cleanup = el("input", { type: "checkbox" });
    cleanup.checked = cfg.delete_remote_after_download !== false;
    const status = el("div", { style: { minHeight: "18px", fontSize: "12px", marginTop: "8px" }});
    const save = el("button", { text: "Save", style: { ...buttonStyle(), background: "#275dad" }});
    const sync = el("button", { text: "Sync remote models", style: buttonStyle() });

    m.body.append(
        el("div", { text: cfg.configured ? "Configured" : "One-time setup required", style: { color: cfg.configured ? "#66d98b" : "#ffbc66", marginBottom: "12px", fontWeight: "700" }}),
        field("Endpoint ID", endpoint),
        field("RunPod API key", apiKey),
        field("Network Volume S3 endpoint", s3Endpoint),
        field("S3 region", s3Region),
        field("Network Volume ID", volume),
        field("S3 access key", access),
        field("S3 secret key", secret),
        el("label", { style: { display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", margin: "8px 0 14px" }}, [cleanup, "Delete remote result after it is downloaded locally"]),
        el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" }}, [save, sync]),
        status,
    );

    save.addEventListener("click", async () => {
        save.disabled = true;
        status.textContent = "Saving…";
        try {
            const payload = {
                endpoint_id: endpoint.value.trim(),
                s3_endpoint: s3Endpoint.value.trim(),
                s3_region: s3Region.value.trim(),
                volume_id: volume.value.trim(),
                delete_remote_after_download: cleanup.checked,
            };
            if (apiKey.value.trim()) payload.runpod_api_key = apiKey.value.trim();
            if (access.value.trim()) payload.s3_access_key = access.value.trim();
            if (secret.value.trim()) payload.s3_secret_key = secret.value.trim();
            const result = await API.post("/runpod-h3/config", payload);
            if (result.model_sync) {
                status.textContent = `Saved. Synced ${result.model_sync.remote_objects ?? result.model_sync.count} remote models. Reload ComfyUI if you just added a model.`;
                status.style.color = "#66d98b";
            } else if (result.model_sync_error) {
                status.textContent = `Saved, but model sync failed: ${result.model_sync_error}`;
                status.style.color = "#ffbc66";
            } else {
                status.textContent = result.configured ? "Saved. Ready." : "Saved, but required fields are still missing.";
                status.style.color = result.configured ? "#66d98b" : "#ffbc66";
            }
        } catch (error) {
            status.textContent = error.message;
            status.style.color = "#ff6b6b";
        } finally {
            save.disabled = false;
        }
    });

    sync.addEventListener("click", async () => {
        sync.disabled = true;
        status.textContent = "Syncing Network Volume model catalog…";
        try {
            const result = await API.post("/runpod-h3/sync-models", {});
            status.textContent = `Synced ${result.remote_objects ?? result.count} remote models. Reload ComfyUI if a new model was added.`;
            status.style.color = "#66d98b";
        } catch (error) {
            status.textContent = error.message || String(error);
            status.style.color = "#ff6b6b";
        } finally {
            sync.disabled = false;
        }
    });
}

async function remoteQueue() {
    let compiled;
    try {
        compiled = await app.graphToPrompt();
        if (!compiled?.output || Object.keys(compiled.output).length === 0) throw new Error("Workflow is empty.");
    } catch (error) {
        const m = modal("RunPod H3");
        m.body.textContent = `Cannot compile workflow: ${error.message}`;
        return;
    }

    const m = modal("RunPod Remote Queue");
    const state = el("div", { text: "Submitting workflow…", style: { fontWeight: "700", marginBottom: "8px" }});
    const detail = el("div", { text: "GPU will wake automatically if no worker is active.", style: { opacity: ".72", fontSize: "12px", marginBottom: "12px" }});
    const timer = el("div", { text: "0s", style: { fontFamily: "monospace", fontSize: "11px", opacity: ".6", marginBottom: "12px" }});
    const cancel = el("button", { text: "Cancel job", style: buttonStyle() });
    cancel.disabled = true;
    m.body.append(state, detail, timer, cancel);

    const started = Date.now();
    const interval = setInterval(() => {
        const seconds = Math.floor((Date.now() - started) / 1000);
        timer.textContent = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    }, 1000);

    let jobId = null;
    let cancelled = false;
    cancel.addEventListener("click", async () => {
        if (!jobId || cancelled) return;
        cancelled = true;
        cancel.disabled = true;
        state.textContent = "Cancelling…";
        try { await API.post(`/runpod-h3/cancel/${encodeURIComponent(jobId)}`, {}); }
        catch (error) { detail.textContent = error.message; }
    });

    try {
        const submit = await API.post("/runpod-h3/submit", { workflow: compiled.output });
        jobId = submit.job_id;
        if (!jobId) throw new Error("RunPod returned no job id.");
        cancel.disabled = false;
        state.textContent = "Queued";
        detail.textContent = `job ${jobId}`;

        let delay = 1500;
        for (;;) {
            await sleep(delay);
            const result = await API.get(`/runpod-h3/status/${encodeURIComponent(jobId)}`);
            const s = result.status || "UNKNOWN";
            if (s === "IN_QUEUE") {
                state.textContent = "Waiting for serverless worker…";
                detail.textContent = `job ${jobId} · cold start/queue`;
                delay = Math.min(4000, delay + 250);
            } else if (s === "IN_PROGRESS" || s === "RUNNING") {
                state.textContent = "Generating on RunPod…";
                const exec = result.execution_time ? ` · ${Math.round(result.execution_time / 1000)}s GPU execution` : "";
                detail.textContent = `job ${jobId}${exec}`;
                delay = 2500;
            } else if (s === "COMPLETED") {
                clearInterval(interval);
                cancel.disabled = true;
                renderOutputs(m.body, result.outputs || [], jobId);
                return;
            } else if (["FAILED", "CANCELLED", "TIMED_OUT"].includes(s)) {
                throw new Error(typeof result.error === "string" ? result.error : JSON.stringify(result.error || s));
            } else if (result.error) {
                throw new Error(result.error);
            }
        }
    } catch (error) {
        clearInterval(interval);
        cancel.disabled = true;
        state.textContent = cancelled ? "Cancelled" : "Run failed";
        state.style.color = cancelled ? "#ffbc66" : "#ff6b6b";
        detail.textContent = error.message || String(error);
    }
}

app.registerExtension({
    name: EXT,
    commands: [
        {
            id: "runpod-h3.queue",
            label: "RunPod Queue",
            function: remoteQueue,
        },
        {
            id: "runpod-h3.settings",
            label: "RunPod H3 Settings",
            function: showSettings,
        },
    ],
    menuCommands: [
        {
            path: ["RunPod H3"],
            commands: ["runpod-h3.queue", "runpod-h3.settings"],
        },
    ],
    actionBarButtons: [
        {
            icon: "icon-[lucide--cloud-upload]",
            label: "RunPod Queue",
            tooltip: "Run the current workflow on RunPod Serverless",
            onClick: remoteQueue,
        },
        {
            icon: "icon-[lucide--settings]",
            tooltip: "RunPod H3 settings",
            onClick: showSettings,
        },
    ],
});
