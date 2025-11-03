const messagesEl = document.getElementById("messages");
const form = document.getElementById("chat-form");
const sendBtn = document.getElementById("send-btn");
const symbolInput = document.getElementById("symbol");
const startInput = document.getElementById("start");
const endInput = document.getElementById("end");

const userTemplate = document.getElementById("user-message-template");
const assistantTemplate = document.getElementById("assistant-message-template");
const FALLBACK_SYMBOL = "510300.SH";
const FALLBACK_YEARS = 4;
const FALLBACK_CASH = 100000;

let defaultInitialCash = FALLBACK_CASH;

function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function appendMessage(template, content) {
    const node = template.content.cloneNode(true);
    node.querySelector(".content").innerHTML = content;
    messagesEl.appendChild(node);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderPlan(plan) {
    const componentsList = plan.components
        .map(
            (comp) =>
                `<li><strong>${escapeHtml(comp.category)}</strong>: ${escapeHtml(comp.objective)}<br/>Rules: ${escapeHtml(
                    comp.rules.join("; ")
                )}</li>`
        )
        .join("");
    return `
        <h4>Strategy Modules</h4>
        <ul>${componentsList}</ul>
        <h4>Structure Diagram</h4>
        <pre>${escapeHtml(plan.diagram)}</pre>
        <h4>Unified YAML Template</h4>
        <pre>${escapeHtml(plan.yaml)}</pre>
        ${plan.reasoning ? `<h4>Notes</h4><p>${escapeHtml(plan.reasoning)}</p>` : ""}
    `;
}

function renderAssistantReply(response) {
    const planHtml = renderPlan(response.plan);
    const backtest = `<h4>Backtest Summary</h4><pre>${escapeHtml(response.backtest_summary)}</pre>`;
    const interpretation = `<h4>AI Interpretation</h4><p>${escapeHtml(response.interpretation)}</p>`;
    return `${planHtml}${backtest}${interpretation}`;
}

async function handleSubmit(evt) {
    evt.preventDefault();
    const prompt = form.prompt.value.trim();
    const symbol = form.symbol.value.trim();
    const start = form.start.value;
    const end = form.end.value;

    if (!prompt) {
        return;
    }

    appendMessage(userTemplate, `<p>${escapeHtml(prompt)}</p>`);

    sendBtn.disabled = true;
    form.classList.add("loading");

    try {
        const payload = {
            message: prompt,
            symbol,
            start_date: start,
            end_date: end,
            initial_cash: defaultInitialCash
        };
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || "Unknown error");
        }
        const data = await res.json();
        appendMessage(assistantTemplate, renderAssistantReply(data));
    } catch (error) {
        appendMessage(
            assistantTemplate,
            `<p class="error">Request failed: ${escapeHtml(error.message)}</p>`
        );
    } finally {
        sendBtn.disabled = false;
        form.classList.remove("loading");
        form.prompt.value = "";
        form.prompt.focus();
    }
}

function setDefaultDates(yearsBack) {
    const endDate = new Date();
    const startDate = new Date();
    startDate.setFullYear(endDate.getFullYear() - yearsBack);

    const format = (d) =>
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
            d.getDate()
        ).padStart(2, "0")}`;

    endInput.value = format(endDate);
    startInput.value = format(startDate);
}

async function loadDefaults() {
    try {
        const res = await fetch("/api/config");
        if (!res.ok) {
            throw new Error("Failed to load defaults");
        }
        const data = await res.json();
        if (data.default_symbol) {
            symbolInput.value = data.default_symbol;
        }
        if (data.default_years) {
            setDefaultDates(data.default_years);
        }
        if (data.initial_cash) {
            defaultInitialCash = data.initial_cash;
        }
    } catch (err) {
        console.warn("Using fallback defaults:", err);
        defaultInitialCash = FALLBACK_CASH;
    }
}

// Prime UI with fallback values instantly, then attempt to load server defaults.
symbolInput.value = FALLBACK_SYMBOL;
setDefaultDates(FALLBACK_YEARS);

form.addEventListener("submit", handleSubmit);
loadDefaults();
