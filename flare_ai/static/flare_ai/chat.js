(() => {
    const root = document.querySelector("[data-flare-ai-chat]");
    if (!root) return;

    const storageKey = "flare-ai-chat-v1";
    const assistantName = root.dataset.assistantName || "Marcott-E";
    const launcher = root.querySelector("[data-ai-launcher]");
    const drawer = root.querySelector("[data-ai-drawer]");
    const closeButton = root.querySelector("[data-ai-close]");
    const resetButton = root.querySelector("[data-ai-reset]");
    const retryButton = root.querySelector("[data-ai-retry]");
    const stopButton = root.querySelector("[data-ai-stop]");
    const sendButton = root.querySelector("[data-ai-send]");
    const messagesElement = root.querySelector("[data-ai-messages]");
    const suggestionsElement = root.querySelector("[data-ai-suggestions]");
    const statusElement = root.querySelector("[data-ai-status]");
    const form = root.querySelector("[data-ai-form]");
    const input = root.querySelector("[data-ai-input]");
    const attachButton = root.querySelector("[data-ai-attach]");
    const attachmentRow = root.querySelector("[data-ai-attachment]");
    const attachmentChip = root.querySelector("[data-ai-attachment-chip]");
    const removeAttachmentButton = root.querySelector("[data-ai-remove-attachment]");
    const fileInput = root.querySelector("[data-ai-file-input]");
    const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]").value;
    let controller = null;
    let activeRequestKind = null;

    const newId = () => {
        if (crypto.randomUUID) return crypto.randomUUID();
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
        return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
    };

    const blankState = () => ({
        conversationId: newId(),
        open: false,
        messages: [],
        suggestions: [],
    });

    const loadState = () => {
        try {
            const value = JSON.parse(sessionStorage.getItem(storageKey));
            if (value && typeof value.conversationId === "string" && Array.isArray(value.messages)) {
                if (!Array.isArray(value.suggestions)) value.suggestions = [];
                return value;
            }
        } catch (_) {
            // Corrupt browser state is safe to discard.
        }
        return blankState();
    };

    let state = loadState();

    const save = () => {
        state.messages = state.messages.slice(-20);
        sessionStorage.setItem(storageKey, JSON.stringify(state));
    };

    // The selected file is held only in this variable while the drawer is open.
    // Browser File objects cannot be persisted to sessionStorage, so closing,
    // navigating or refreshing always discards the attachment.
    let attachmentFile = null;

    const formatBytes = (bytes) => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const renderAttachment = () => {
        if (attachmentFile) {
            attachmentChip.textContent = `${attachmentFile.name} · ${formatBytes(attachmentFile.size)}`;
            attachmentRow.hidden = false;
        } else {
            attachmentChip.textContent = "";
            attachmentRow.hidden = true;
        }
    };

    const setAttachmentFile = (file) => {
        attachmentFile = file || null;
        renderAttachment();
    };

    const clearAttachment = () => {
        attachmentFile = null;
        fileInput.value = "";
        renderAttachment();
    };

    const appendInlineMarkdown = (container, text) => {
        const pattern = /`([^`\n]+)`|\[([^\]\n]+)\]\\?\(([^)\s]+)\)|\*\*([^*\n](?:[^\n]*?[^*\n])?)\*\*|__([^_\n](?:[^\n]*?[^_\n])?)__|~~([^~\n](?:[^\n]*?[^~\n])?)~~|\*([^*\n]+)\*|_([^_\n]+)_/g;
        let index = 0;
        for (const match of text.matchAll(pattern)) {
            container.append(document.createTextNode(text.slice(index, match.index)));
            if (match[1] !== undefined) {
                const code = document.createElement("code");
                code.textContent = match[1];
                container.append(code);
            } else if (match[2] !== undefined) {
                try {
                    const url = new URL(match[3], window.location.origin);
                    if (url.origin !== window.location.origin || !match[3].startsWith("/")) {
                        throw new Error("unsafe URL");
                    }
                    const link = document.createElement("a");
                    link.href = `${url.pathname}${url.search}${url.hash}`;
                    appendInlineMarkdown(link, match[2]);
                    container.append(link);
                } catch (_) {
                    container.append(document.createTextNode(match[0]));
                }
            } else {
                const tag = match[4] !== undefined || match[5] !== undefined
                    ? "strong"
                    : match[6] !== undefined
                        ? "del"
                        : "em";
                const value = match[4] ?? match[5] ?? match[6] ?? match[7] ?? match[8];
                const element = document.createElement(tag);
                appendInlineMarkdown(element, value);
                container.append(element);
            }
            index = match.index + match[0].length;
        }
        container.append(document.createTextNode(text.slice(index)));
    };

    const appendParagraph = (container, lines, tag = "p") => {
        if (!lines.length) return;
        const paragraph = document.createElement(tag);
        appendInlineMarkdown(paragraph, lines.join("\n"));
        container.append(paragraph);
    };

    const renderMarkdown = (container, markdown) => {
        const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
        let paragraph = [];
        const flushParagraph = () => {
            appendParagraph(container, paragraph);
            paragraph = [];
        };

        for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
            const line = lines[lineIndex];
            const fence = line.match(/^\s*```([\w+-]*)\s*$/);
            if (fence) {
                flushParagraph();
                const codeLines = [];
                lineIndex += 1;
                while (lineIndex < lines.length && !/^\s*```\s*$/.test(lines[lineIndex])) {
                    codeLines.push(lines[lineIndex]);
                    lineIndex += 1;
                }
                const pre = document.createElement("pre");
                const code = document.createElement("code");
                if (fence[1]) code.dataset.language = fence[1];
                code.textContent = codeLines.join("\n");
                pre.append(code);
                container.append(pre);
                continue;
            }

            const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
            if (heading) {
                flushParagraph();
                appendParagraph(container, [heading[2]], `h${heading[1].length}`);
                continue;
            }

            if (/^\s{0,3}((\*|-|_)\s*){3,}$/.test(line)) {
                flushParagraph();
                container.append(document.createElement("hr"));
                continue;
            }

            const quote = line.match(/^\s{0,3}>\s?(.*)$/);
            if (quote) {
                flushParagraph();
                const quoteLines = [quote[1]];
                while (lineIndex + 1 < lines.length) {
                    const nextQuote = lines[lineIndex + 1].match(/^\s{0,3}>\s?(.*)$/);
                    if (!nextQuote) break;
                    quoteLines.push(nextQuote[1]);
                    lineIndex += 1;
                }
                const blockquote = document.createElement("blockquote");
                appendParagraph(blockquote, quoteLines);
                container.append(blockquote);
                continue;
            }

            const listItem = line.match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
            if (listItem) {
                flushParagraph();
                const ordered = /^\d/.test(listItem[1]);
                const list = document.createElement(ordered ? "ol" : "ul");
                let current = listItem;
                while (current) {
                    const item = document.createElement("li");
                    appendInlineMarkdown(item, current[2]);
                    list.append(item);
                    if (lineIndex + 1 >= lines.length) break;
                    const next = lines[lineIndex + 1].match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
                    if (!next || /^\d/.test(next[1]) !== ordered) break;
                    current = next;
                    lineIndex += 1;
                }
                container.append(list);
                continue;
            }

            if (!line.trim()) {
                flushParagraph();
            } else {
                paragraph.push(line);
            }
        }
        flushParagraph();
    };

    const render = () => {
        drawer.hidden = !state.open;
        launcher.setAttribute("aria-expanded", String(state.open));
        messagesElement.replaceChildren();
        for (const message of state.messages) {
            const item = document.createElement("li");
            item.className = `flare-ai-message flare-ai-message-${message.role}`;
            if (message.state === "interrupted") item.classList.add("flare-ai-message-interrupted");
            if (message.state === "error") item.classList.add("flare-ai-message-error");
            const label = document.createElement("span");
            label.className = "flare-ai-message-label";
            label.textContent = message.role === "user" ? "You" : assistantName;
            const content = document.createElement("div");
            content.className = "flare-ai-message-content";
            const text = message.text || (message.state === "pending" ? "…" : "");
            if (message.role === "assistant" && message.state === "pending" && !message.text) {
                const loading = document.createElement("span");
                loading.className = "flare-ai-loading";
                loading.setAttribute("role", "img");
                loading.setAttribute("aria-label", message.kind === "greeting" ? `Loading ${assistantName}` : `${assistantName} is responding`);
                loading.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
                content.replaceChildren(loading);
            } else if (message.role === "assistant") {
                renderMarkdown(content, text);
            } else {
                content.textContent = text;
                if (message.attachmentName) {
                    const attached = document.createElement("div");
                    attached.className = "flare-ai-message-attachment";
                    attached.textContent = `Attached: ${message.attachmentName}`;
                    content.append(attached);
                }
            }
            item.append(label, content);
            messagesElement.append(item);
        }

        suggestionsElement.replaceChildren();
        if (state.suggestions.length) {
            const heading = document.createElement("p");
            heading.textContent = "Try asking:";
            suggestionsElement.append(heading);
            for (const question of state.suggestions) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "flare-ai-suggestion";
                button.textContent = question;
                button.addEventListener("click", () => send(question));
                suggestionsElement.append(button);
            }
        }
        suggestionsElement.hidden = !state.suggestions.length;

        const last = state.messages.at(-1);
        const canRetry = last && ["interrupted", "error"].includes(last.state);
        retryButton.hidden = !canRetry;
        if (canRetry) {
            retryButton.textContent = last.kind === "greeting"
                ? `Retry loading ${assistantName}`
                : "Retry message";
        }
        messagesElement.scrollTop = messagesElement.scrollHeight;
    };

    const setOpen = (open) => {
        state.open = open;
        if (!open) clearAttachment();
        save();
        render();
        if (open) {
            input.focus();
            if (!state.messages.length) sendGreeting();
        }
    };

    const setBusy = (busy, kind = null) => {
        activeRequestKind = busy ? kind : null;
        sendButton.disabled = busy;
        input.disabled = busy;
        stopButton.hidden = !busy;
        statusElement.textContent = busy
            ? kind === "greeting" ? `Loading ${assistantName}…` : `${assistantName} is responding…`
            : "";
    };

    const markInterrupted = () => {
        const last = state.messages.at(-1);
        if (last && last.role === "assistant" && last.state === "pending") {
            last.state = "interrupted";
            if (!last.text) last.text = "Response interrupted.";
            save();
            render();
        }
    };

    const send = async (providedMessage = null, options = {}) => {
        if (controller) return;
        const greeting = options.greeting === true;
        const text = greeting ? "" : (providedMessage ?? input.value).trim();
        if (!greeting && !text) return;

        input.value = "";
        state.suggestions = [];
        if (!greeting) {
            state.messages.push({
                role: "user",
                text,
                state: "complete",
                attachmentName: attachmentFile ? attachmentFile.name : null,
            });
        }
        state.messages.push({
            role: "assistant",
            text: "",
            state: "pending",
            kind: greeting ? "greeting" : "reply",
        });
        save();
        render();
        setBusy(true, greeting ? "greeting" : "reply");
        controller = new AbortController();
        const requestController = controller;

        try {
            const requestConfig = {
                method: "POST",
                credentials: "same-origin",
                signal: requestController.signal,
                headers: {
                    "Accept": "application/x-ndjson",
                    "X-CSRFToken": csrfToken,
                },
            };
            if (!greeting && attachmentFile) {
                // Let the browser set the multipart Content-Type boundary.
                const body = new FormData();
                body.append("conversation_id", state.conversationId);
                body.append("page_path", window.location.pathname);
                body.append("message", text);
                body.append("attachment", attachmentFile);
                requestConfig.body = body;
            } else {
                requestConfig.headers["Content-Type"] = "application/json";
                const requestBody = {
                    conversation_id: state.conversationId,
                    page_path: window.location.pathname,
                };
                if (greeting) {
                    requestBody.greeting = true;
                } else {
                    requestBody.message = text;
                }
                requestConfig.body = JSON.stringify(requestBody);
            }
            const response = await fetch(root.dataset.streamUrl, requestConfig);
            if (!response.ok || !response.body) {
                let detail = "The assistant request failed.";
                try {
                    const body = await response.json();
                    if (body.error) detail = body.error;
                } catch (_) {}
                throw new Error(detail);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            while (true) {
                const {value, done} = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";
                for (const line of lines) {
                    if (!line) continue;
                    const event = JSON.parse(line);
                    const assistant = state.messages.at(-1);
                    if (event.type === "delta") {
                        if (!assistant.text && activeRequestKind === "greeting") {
                            statusElement.textContent = `${assistantName} is responding…`;
                        }
                        assistant.text += event.text;
                        render();
                    } else if (event.type === "done") {
                        assistant.state = "complete";
                        if (Array.isArray(event.suggestions)) {
                            state.suggestions = event.suggestions.filter((value) => typeof value === "string");
                        }
                    } else if (event.type === "error") {
                        assistant.text = event.message;
                        assistant.state = "error";
                    }
                    save();
                }
                if (done) break;
            }
            const assistant = state.messages.at(-1);
            if (assistant && assistant.state === "pending") assistant.state = "complete";
        } catch (error) {
            if (error.name === "AbortError") {
                markInterrupted();
            } else {
                const assistant = state.messages.at(-1);
                assistant.text = error.message || "The assistant request failed.";
                assistant.state = "error";
            }
        } finally {
            if (controller === requestController) {
                controller = null;
                setBusy(false);
            }
            save();
            render();
        }
    };

    const sendGreeting = () => {
        const last = state.messages.at(-1);
        if (controller || state.messages.length && last?.kind !== "greeting") return;
        if (last?.kind === "greeting") {
            state.messages.pop();
            state.conversationId = newId();
        }
        send(null, {greeting: true});
    };

    launcher.addEventListener("click", () => setOpen(!state.open));
    closeButton.addEventListener("click", () => setOpen(false));
    stopButton.addEventListener("click", () => controller?.abort());
    attachButton.addEventListener("click", () => {
        // Reset so the same file can be re-selected, then open the picker.
        fileInput.value = "";
        fileInput.click();
    });
    fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        if (file) setAttachmentFile(file);
    });
    removeAttachmentButton.addEventListener("click", () => clearAttachment());
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        send();
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });
    retryButton.addEventListener("click", () => {
        const last = state.messages.at(-1);
        if (last?.kind === "greeting") {
            sendGreeting();
            return;
        }
        const previous = [...state.messages].reverse().find((message) => message.role === "user");
        if (previous) send(previous.text);
    });
    resetButton.addEventListener("click", async () => {
        if (controller) {
            const previousController = controller;
            previousController.abort();
            while (controller === previousController) {
                await new Promise((resolve) => setTimeout(resolve, 0));
            }
        }
        const oldId = state.conversationId;
        state = blankState();
        state.open = true;
        clearAttachment();
        save();
        render();
        setBusy(true, "greeting");
        try {
            const response = await fetch(root.dataset.resetUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify({conversation_id: oldId}),
            });
            if (!response.ok) throw new Error("The conversation could not be reset.");
            setBusy(false);
            sendGreeting();
        } catch (error) {
            setBusy(false);
            state.messages.push({
                role: "assistant",
                text: error.message || "The conversation could not be reset.",
                state: "error",
                kind: "greeting",
            });
            save();
            render();
        }
    });
    window.addEventListener("pagehide", () => {
        if (controller) {
            markInterrupted();
            controller.abort();
        }
    });

    render();
    if (state.open && !state.messages.length) sendGreeting();
})();
