/* global frappe */
/**
 * AI Assistant Chat Widget for ERPNext v15
 *
 * Renders a floating chat button + panel and communicates with
 * ai_assistant.api.chat.send_message via frappe.call().
 */

(function () {
    "use strict";

    const STORAGE_KEY = "ai_chat_session_id";
    const ANSWER_MODE_STORAGE_KEY = "ai_chat_answer_mode";
    const VOICE_OUTPUT_STORAGE_KEY = "ai_chat_voice_output";
    const LANGUAGE_MODE_STORAGE_KEY = "ai_chat_language_mode";
    // Placeholder, will be replaced by backend preferences
    const DEFAULT_CHAT_PREFERENCES = {
        default_answer_mode: "summary",
        answer_mode_text_block: "",
        answer_modes: [],
        widget_enabled: true,
        agent_name: "AI Assistant",
        agent_title: "AI Assistant",
        quick_actions: [],
        suggested_actions: [],
    };
    let QUICK_TOPIC_OPTIONS = [];
    const USER_INTEREST_STORAGE_PREFIX = "ai_chat_user_interests_v1";
    const TOPIC_INTEREST_KEYWORDS = {
        "pending-approvals": ["approval", "approve", "pending", "workflow", "task", "موافقة", "اعتماد", "معلق"],
        "sales-summary": ["sales", "invoice", "order", "revenue", "فاتورة", "مبيعات", "طلب"],
        "bank-balance": ["bank", "balance", "cash", "payment", "رصيد", "بنك", "نقد"],
        "stock-status": ["stock", "inventory", "warehouse", "item", "مخزون", "مستودع", "صنف"],
        "hr-workflow": ["hr", "leave", "attendance", "employee", "موظف", "اجاز", "حضور"],
        permissions: ["permission", "access", "role", "allow", "صلاحية", "دور", "وصول"],
    };
    const DEFAULT_EMPTY_STATE_TEXT = "Ask in normal language about sales, stock, accounting, workflows, and permissions.";
    const DEFAULT_CALL_TRANSCRIPT_TEXT = "Conversation transcript will appear here while call mode is active.";

    function getCurrentUserIdForInterestKey() {
        const user =
            typeof frappe !== "undefined" && frappe.session && frappe.session.user
                ? String(frappe.session.user || "")
                : "";
        return (user || "anonymous").trim().toLowerCase();
    }

    function getUserInterestStorageKey() {
        return `${USER_INTEREST_STORAGE_PREFIX}:${getCurrentUserIdForInterestKey()}`;
    }

    function loadUserInterests() {
        const fallback = { topic_scores: {} };
        try {
            if (typeof window === "undefined" || !window.localStorage) {
                return fallback;
            }

            const raw = window.localStorage.getItem(getUserInterestStorageKey());
            if (!raw) {
                return fallback;
            }

            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object") {
                return fallback;
            }

            const topicScores = parsed.topic_scores && typeof parsed.topic_scores === "object"
                ? parsed.topic_scores
                : {};

            return { topic_scores: topicScores };
        } catch (error) {
            return fallback;
        }
    }

    function saveUserInterests(interests) {
        try {
            if (typeof window === "undefined" || !window.localStorage) {
                return;
            }
            window.localStorage.setItem(getUserInterestStorageKey(), JSON.stringify(interests || { topic_scores: {} }));
        } catch (error) {
            // Ignore storage failures to keep chat functional.
        }
    }

    function increaseTopicInterest(topicKey, amount) {
        const normalizedKey = String(topicKey || "").trim();
        if (!normalizedKey) {
            return;
        }

        const interests = loadUserInterests();
        const current = Number(interests.topic_scores[normalizedKey]) || 0;
        interests.topic_scores[normalizedKey] = Math.max(0, current + (Number(amount) || 1));
        saveUserInterests(interests);
    }

    function learnUserInterestsFromQuestion(question) {
        const text = String(question || "").trim().toLowerCase();
        if (!text) {
            return;
        }

        Object.keys(TOPIC_INTEREST_KEYWORDS).forEach(function (topicKey) {
            const keywords = TOPIC_INTEREST_KEYWORDS[topicKey] || [];
            const matched = keywords.some(function (keyword) {
                const token = String(keyword || "").trim().toLowerCase();
                return token && text.indexOf(token) !== -1;
            });
            if (matched) {
                increaseTopicInterest(topicKey, 1);
            }
        });
    }

    function rankTopicOptionsByInterest(options) {
        const source = Array.isArray(options) ? options.slice() : [];
        const interests = loadUserInterests();
        const topicScores = interests.topic_scores || {};

        return source
            .map(function (topic, index) {
                return {
                    topic: topic,
                    index: index,
                    score: Number(topicScores[String(topic && topic.key ? topic.key : "")]) || 0,
                };
            })
            .sort(function (a, b) {
                if (b.score !== a.score) {
                    return b.score - a.score;
                }
                return a.index - b.index;
            })
            .map(function (row) {
                return row.topic;
            });
    }

    function getUserDisplayName() {
        const fullName =
            typeof frappe !== "undefined" && frappe.session && frappe.session.user_fullname
                ? String(frappe.session.user_fullname || "")
                : "";
        const userId =
            typeof frappe !== "undefined" && frappe.session && frappe.session.user
                ? String(frappe.session.user || "")
                : "";

        const candidate = (fullName || userId || "").trim();
        if (!candidate) {
            return "there";
        }

        if (candidate.indexOf("@") !== -1) {
            return candidate.split("@")[0];
        }

        return candidate.split(" ")[0] || candidate;
    }

    function normalizeTopicOption(row) {
        const source = row && typeof row === "object" ? row : {};
        const key = String(source.key || "")
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9_-]/g, "")
            .slice(0, 60);
        const label = String(source.label || "").trim().slice(0, 80);
        const prompt = String(source.prompt || "").trim().slice(0, 240);

        if (!key || !label || !prompt) {
            return null;
        }

        return { key: key, label: label, prompt: prompt };
    }

    function mergeQuickTopicOptions(dynamicOptions) {
        const merged = [];
        const seen = new Set();

        function pushTopic(option) {
            const normalized = normalizeTopicOption(option);
            if (!normalized || seen.has(normalized.key)) {
                return;
            }
            seen.add(normalized.key);
            merged.push(normalized);
        }

        QUICK_TOPIC_OPTIONS.forEach(pushTopic);
        if (Array.isArray(dynamicOptions)) {
            dynamicOptions.forEach(pushTopic);
        }

        return rankTopicOptionsByInterest(merged).slice(0, 12);
    }

    function renderQuickTopicOptions(containerEl, options, onTopicSelect) {
        if (!containerEl) {
            return;
        }

        containerEl.innerHTML = "";

        (options || []).slice(0, 8).forEach(function (topic) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ai-chat-topic-chip";
            button.textContent = topic.label;
            button.title = topic.prompt;
            button.addEventListener("click", function () {
                if (typeof onTopicSelect === "function") {
                    onTopicSelect(topic);
                }
            });
            containerEl.appendChild(button);
        });
    }

    let initRetryCount = 0;
    const MAX_INIT_RETRIES = 20;
    const MAX_HISTORY_TURNS = 20;
    const SERVER_RECORDING_MAX_MS = 9000;

    let selectedAnswerMode = DEFAULT_CHAT_PREFERENCES.default_answer_mode;
    let conversationHistory = [];
    let widgetBlockedByRole = false;
    let voiceOutputEnabled = false;
    let latestAssistantReply = "";
    let lastSpeechLanguageHint = "";
    let conversationLanguageHint = "";
    let conversationLanguageMode = "detect";

    // -----------------------------------------------------------------------
    // Session + preference helpers
    // -----------------------------------------------------------------------
    function getSessionId() {
        let sid = sessionStorage.getItem(STORAGE_KEY);
        if (!sid) {
            sid = startNewSessionId();
        }
        return sid;
    }

    function startNewSessionId() {
        const sid = frappe.utils.get_random(20);
        sessionStorage.setItem(STORAGE_KEY, sid);
        return sid;
    }

    function getStoredAnswerMode() {
        return sessionStorage.getItem(ANSWER_MODE_STORAGE_KEY) || "";
    }

    function setStoredAnswerMode(mode) {
        sessionStorage.setItem(ANSWER_MODE_STORAGE_KEY, mode);
    }

    function getStoredVoiceOutputEnabled() {
        return sessionStorage.getItem(VOICE_OUTPUT_STORAGE_KEY) === "1";
    }

    function setStoredVoiceOutputEnabled(enabled) {
        sessionStorage.setItem(VOICE_OUTPUT_STORAGE_KEY, enabled ? "1" : "0");
    }

    function getStoredLanguageMode() {
        const mode = String(sessionStorage.getItem(LANGUAGE_MODE_STORAGE_KEY) || "detect").toLowerCase();
        if (mode === "ar" || mode === "en" || mode === "zh" || mode === "detect") {
            return mode;
        }
        return "detect";
    }

    function setStoredLanguageMode(mode) {
        const normalized = mode === "ar" || mode === "en" || mode === "zh" ? mode : "detect";
        sessionStorage.setItem(LANGUAGE_MODE_STORAGE_KEY, normalized);
    }

    function normalizeSpeechLanguage(lang) {
        const value = String(lang || "").trim();
        if (!value) return "";

        const lower = value.toLowerCase();
        if (lower === "ar" || lower.startsWith("ar-")) {
            return "ar-SA";
        }
        if (lower === "en" || lower.startsWith("en-")) {
            return "en-US";
        }
        if (lower === "zh" || lower.startsWith("zh-") || lower === "chinese") {
            return "zh-CN";
        }
        return value;
    }

    function languageCodeFromTag(lang) {
        const normalized = normalizeSpeechLanguage(lang).toLowerCase();
        if (normalized.startsWith("ar")) {
            return "ar";
        }
        if (normalized.startsWith("en")) {
            return "en";
        }
        if (normalized.startsWith("zh")) {
            return "zh";
        }
        return "";
    }

    function textLooksArabic(text) {
        return /[\u0600-\u06FF]/.test(String(text || ""));
    }

    function textLooksLatin(text) {
        return /[A-Za-z]/.test(String(text || ""));
    }

    function textLooksChinese(text) {
        return /[\u4E00-\u9FFF\u3400-\u4DBF]/.test(String(text || ""));
    }

    function inferLanguageFromText(text) {
        const value = String(text || "").trim();
        if (!value) {
            return "";
        }
        // Count Arabic, Chinese, and Latin characters
        let arabicCount = 0, chineseCount = 0, latinCount = 0, total = 0;
        for (let i = 0; i < value.length; i++) {
            const code = value.charCodeAt(i);
            total++;
            if (code >= 0x0600 && code <= 0x06FF) arabicCount++;
            else if ((code >= 0x4E00 && code <= 0x9FFF) || (code >= 0x3400 && code <= 0x4DBF)) chineseCount++;
            else if ((code >= 65 && code <= 90) || (code >= 97 && code <= 122)) latinCount++;
        }
        // Heuristic: if >30% of chars are a script, pick that
        if (arabicCount / total > 0.3) return "ar-SA";
        if (chineseCount / total > 0.3) return "zh-CN";
        if (latinCount / total > 0.3) return "en-US";
        // Fallback: check for any Arabic/Chinese/Latin
        if (arabicCount > 0) return "ar-SA";
        if (chineseCount > 0) return "zh-CN";
        if (latinCount > 0) return "en-US";
        // Fallback: browser language
        if (navigator.language && navigator.language.startsWith("ar")) return "ar-SA";
        if (navigator.language && navigator.language.startsWith("zh")) return "zh-CN";
        if (navigator.language && navigator.language.startsWith("en")) return "en-US";
        return "";
    }

    function detectInitialConversationLanguage() {
        const htmlDir = String(document.documentElement.getAttribute("dir") || "").toLowerCase();
        if (htmlDir === "rtl") {
            return "ar";
        }

        const candidates = [
            document.documentElement.getAttribute("lang"),
            typeof frappe !== "undefined" && frappe.boot ? frappe.boot.lang : "",
            navigator.language || "",
        ];

        for (let i = 0; i < candidates.length; i += 1) {
            const code = languageCodeFromTag(candidates[i]);
            if (code) {
                return code;
            }
        }

        return "en";
    }

    function getConversationLanguageCode() {
        if (conversationLanguageMode === "ar" || conversationLanguageMode === "en" || conversationLanguageMode === "zh") {
            return conversationLanguageMode;
        }

        if (!conversationLanguageHint) {
            conversationLanguageHint = detectInitialConversationLanguage();
        }
        return conversationLanguageHint || "en";
    }

    function getLanguageHintForRequest(text) {
        if (conversationLanguageMode === "ar" || conversationLanguageMode === "en" || conversationLanguageMode === "zh") {
            return conversationLanguageMode;
        }

        const fromText = languageCodeFromTag(inferLanguageFromText(text));
        if (fromText) {
            return fromText;
        }
        return getConversationLanguageCode();
    }

    function setConversationLanguageFromTag(lang) {
        const code = languageCodeFromTag(lang);
        if (code && conversationLanguageMode === "detect") {
            conversationLanguageHint = code;
        }
    }

    function updateConversationLanguageFromText(text) {
        if (conversationLanguageMode !== "detect") {
            conversationLanguageHint = conversationLanguageMode;
            return;
        }

        const inferred = languageCodeFromTag(inferLanguageFromText(text));
        if (inferred) {
            conversationLanguageHint = inferred;
        } else if (!conversationLanguageHint) {
            conversationLanguageHint = detectInitialConversationLanguage();
        }
    }

    function setConversationLanguageMode(mode, textarea, selectEl) {
        const normalized = mode === "ar" || mode === "en" || mode === "zh" ? mode : "detect";
        conversationLanguageMode = normalized;
        setStoredLanguageMode(normalized);

        if (normalized === "detect") {
            if (!conversationLanguageHint) {
                conversationLanguageHint = detectInitialConversationLanguage();
            }
        } else {
            conversationLanguageHint = normalized;
            rememberSpeechLanguageFromText(
                normalized === "ar" ? "مرحبا" : normalized === "zh" ? "你好" : "hello"
            );
        }

        if (selectEl) {
            selectEl.value = normalized;
        }
        applyComposerLanguage(textarea);
    }

    function applyComposerLanguage(textarea) {
        if (!textarea) {
            return;
        }

        const languageCode = getConversationLanguageCode();
        const isArabic = languageCode === "ar";
        const isChinese = languageCode === "zh";

        textarea.setAttribute("lang", isArabic ? "ar" : isChinese ? "zh" : "en");
        textarea.setAttribute("dir", isArabic ? "rtl" : "ltr");
        textarea.classList.toggle("ai-chat-input-rtl", isArabic);

        if (!String(textarea.value || "").trim()) {
            if (conversationLanguageMode === "detect") {
                textarea.placeholder = isArabic
                    ? "اكتب رسالتك واضغط Enter للإرسال..."
                    : isChinese
                    ? "输入消息后按 Enter 发送..."
                    : "Write your message and press Enter to send...";
            } else {
                textarea.placeholder = isArabic
                    ? "اكتب رسالتك بالعربية واضغط Enter للإرسال..."
                    : isChinese
                    ? "请用中文输入消息后按 Enter 发送..."
                    : "Write your message in English and press Enter to send...";
            }
        }
    }

    function getSpeechLanguageCandidates(textHint) {
        const unique = [];
        const seen = new Set();
        const htmlDir = String(document.documentElement.getAttribute("dir") || "").toLowerCase();

        function addCandidate(lang) {
            const normalized = normalizeSpeechLanguage(lang);
            if (!normalized || seen.has(normalized)) {
                return;
            }
            seen.add(normalized);
            unique.push(normalized);
        }

        addCandidate(inferLanguageFromText(textHint));
        addCandidate(lastSpeechLanguageHint);
        addCandidate(getConversationLanguageCode());

        if (htmlDir === "rtl") {
            addCandidate("ar-SA");
        }

        addCandidate(document.documentElement.getAttribute("lang"));
        addCandidate(typeof frappe !== "undefined" && frappe.boot ? frappe.boot.lang : "");

        if (Array.isArray(navigator.languages)) {
            navigator.languages.forEach(function (lang) {
                addCandidate(lang);
            });
        }

        addCandidate(navigator.language || "");

        // Keep both major candidates for automatic retries.
        addCandidate("ar-SA");
        addCandidate("en-US");
        addCandidate("zh-CN");

        return unique.length ? unique : ["en-US"];
    }

    function getSpeechLanguage() {
        return getSpeechLanguageCandidates("")[0] || "en-US";
    }

    function rememberSpeechLanguageFromText(text) {
        const inferred = inferLanguageFromText(text);
        if (inferred) {
            lastSpeechLanguageHint = inferred;
            if (conversationLanguageMode === "detect") {
                setConversationLanguageFromTag(inferred);
            }
        }
    }

    function speechErrorMessage(errorCode) {
        switch (String(errorCode || "")) {
            case "not-allowed":
            case "service-not-allowed":
                return "تعذر استخدام الميكروفون. تأكد من السماح بإذن الميكروفون للمتصفح ثم حاول مرة أخرى.";
            case "no-speech":
                return "لم يتم التقاط صوت واضح. تحدث بالقرب من الميكروفون ثم حاول مرة أخرى.";
            case "audio-capture":
                return "لم يتم العثور على ميكروفون متاح. تحقق من جهاز الصوت ثم أعد المحاولة.";
            case "network":
                return "حدثت مشكلة شبكة أثناء التعرف على الصوت. حاول مرة أخرى.";
            default:
                return "تعذر تحويل الصوت إلى نص. حاول مرة أخرى.";
        }
    }

    function supportsSpeechRecognition() {
        return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    }

    function supportsSpeechSynthesis() {
        return typeof window.speechSynthesis !== "undefined" && typeof window.SpeechSynthesisUtterance === "function";
    }

    function supportsServerAudioCapture() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && typeof window.MediaRecorder !== "undefined");
    }

    function chooseRecorderMimeType() {
        if (typeof window.MediaRecorder === "undefined" || typeof window.MediaRecorder.isTypeSupported !== "function") {
            return "";
        }

        const candidates = [
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/ogg;codecs=opus",
            "audio/ogg",
            "audio/mp4",
        ];

        for (let i = 0; i < candidates.length; i += 1) {
            if (window.MediaRecorder.isTypeSupported(candidates[i])) {
                return candidates[i];
            }
        }

        return "";
    }

    function blobToBase64(blob) {
        return new Promise(function (resolve, reject) {
            const reader = new FileReader();
            reader.onload = function () {
                const result = String(reader.result || "");
                const commaIndex = result.indexOf(",");
                const base64 = commaIndex >= 0 ? result.slice(commaIndex + 1) : result;
                resolve(base64 || "");
            };
            reader.onerror = function () {
                reject(new Error("blob-read-error"));
            };
            reader.readAsDataURL(blob);
        });
    }

    function recordAudioClip(maxDurationMs, onStopReady) {
        return new Promise(async function (resolve, reject) {
            if (!supportsServerAudioCapture()) {
                reject(new Error("capture-not-supported"));
                return;
            }

            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                    },
                });
            } catch (error) {
                reject(new Error("mic-permission"));
                return;
            }

            const mimeType = chooseRecorderMimeType();
            let recorder;
            try {
                recorder = mimeType
                    ? new window.MediaRecorder(stream, { mimeType: mimeType, audioBitsPerSecond: 32000 })
                    : new window.MediaRecorder(stream);
            } catch (error) {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
                reject(new Error("recorder-init"));
                return;
            }

            const chunks = [];
            let finished = false;

            function cleanup() {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
                if (typeof onStopReady === "function") {
                    onStopReady(null);
                }
            }

            function fail(error) {
                if (finished) return;
                finished = true;
                cleanup();
                reject(error);
            }

            recorder.ondataavailable = function (event) {
                if (event.data && event.data.size > 0) {
                    chunks.push(event.data);
                }
            };

            recorder.onerror = function () {
                fail(new Error("recording-error"));
            };

            recorder.onstop = async function () {
                if (finished) return;
                finished = true;
                cleanup();

                try {
                    const blob = new Blob(chunks, {
                        type: recorder.mimeType || mimeType || "audio/webm",
                    });

                    if (!blob.size) {
                        reject(new Error("empty-audio"));
                        return;
                    }

                    const audioBase64 = await blobToBase64(blob);
                    if (!audioBase64) {
                        reject(new Error("empty-audio"));
                        return;
                    }

                    resolve({
                        audioBase64: audioBase64,
                        mimeType: blob.type || recorder.mimeType || "audio/webm",
                    });
                } catch (error) {
                    reject(new Error("blob-convert"));
                }
            };

            const stopRecording = function () {
                if (recorder.state !== "inactive") {
                    recorder.stop();
                }
            };

            if (typeof onStopReady === "function") {
                onStopReady(stopRecording);
            }

            recorder.start();
            window.setTimeout(stopRecording, maxDurationMs);
        });
    }

    function serverTranscriptionErrorMessage(errorCode) {
        switch (String(errorCode || "")) {
            case "capture-not-supported":
                return "المتصفح لا يدعم تسجيل الصوت في هذه الصفحة.";
            case "mic-permission":
                return "تعذر الوصول إلى الميكروفون. اسمح بالإذن من المتصفح ثم حاول مرة أخرى.";
            case "recorder-init":
            case "recording-error":
                return "حدث خطأ أثناء تسجيل الصوت. حاول مرة أخرى.";
            case "empty-audio":
                return "لم يتم التقاط صوت واضح. حاول التحدث بصوت أعلى.";
            case "blob-convert":
                return "تعذر تجهيز ملف الصوت للإرسال. حاول مرة أخرى.";
            default:
                return "تعذر تحويل الصوت إلى نص. حاول مرة أخرى.";
        }
    }

    function transcribeAudioOnServer(audioBase64, mimeType, languageHint) {
        return new Promise(function (resolve, reject) {
            frappe.call({
                method: "ai_assistant.api.chat.transcribe_audio",
                args: {
                    audio_base64: audioBase64,
                    mime_type: mimeType,
                    language: languageHint || "",
                },
                callback: function (r) {
                    const text = r && r.message ? String(r.message.text || "").trim() : "";
                    if (!text) {
                        reject(new Error("empty-audio"));
                        return;
                    }
                    resolve(text);
                },
                error: function (r) {
                    const fallback = "تعذر تحويل الصوت إلى نص عبر الخادم. حاول مرة أخرى.";
                    const serverMessage = r && r.message ? String(r.message) : "";
                    reject(new Error(serverMessage || fallback));
                },
            });
        });
    }

    function stopSpeaking() {
        if (supportsSpeechSynthesis()) {
            window.speechSynthesis.cancel();
        }
    }

    function speakText(text) {
        if (!voiceOutputEnabled || !supportsSpeechSynthesis()) {
            return Promise.resolve();
        }

        const spokenText = String(text || "").trim();
        if (!spokenText) {
            return Promise.resolve();
        }

        stopSpeaking();

        return new Promise(function (resolve) {
            const utterance = new window.SpeechSynthesisUtterance(
                spokenText.length > 1600 ? `${spokenText.slice(0, 1600)}...` : spokenText
            );
            // Detect language from the AI reply text first so Arabic replies are
            // spoken with an Arabic voice even when the user asked in English.
            utterance.lang = inferLanguageFromText(spokenText) || lastSpeechLanguageHint || getSpeechLanguage();
            utterance.rate = 1;
            utterance.pitch = 1;
            utterance.onend = function () {
                resolve();
            };
            utterance.onerror = function () {
                resolve();
            };
            window.speechSynthesis.speak(utterance);
        });
    }

    function updateVoiceOutputButtonState(button) {
        if (!button) return;
        button.classList.toggle("is-active", voiceOutputEnabled);
        button.setAttribute("aria-pressed", voiceOutputEnabled ? "true" : "false");
        button.title = voiceOutputEnabled ? "Voice output on" : "Voice output off";
    }

    function updateMicButtonState(button, isListening) {
        if (!button) return;
        button.classList.toggle("is-active", isListening);
        button.dataset.listening = isListening ? "1" : "0";
        button.setAttribute("aria-pressed", isListening ? "true" : "false");
        button.title = isListening ? "Stop voice input" : "Start voice input";
    }

    function updateLiveVoiceButtonState(button, enabled) {
        if (!button) return;
        button.classList.toggle("is-active", enabled);
        button.setAttribute("aria-pressed", enabled ? "true" : "false");
        button.title = enabled ? "Stop talk mode" : "Start talk mode";
        button.setAttribute("aria-label", enabled ? "Stop talk mode" : "Start talk mode");
    }

    function createSpeechRecognizer(textarea, micBtn, hooks) {
        if (!supportsSpeechRecognition()) {
            return null;
        }

        const eventHooks = hooks || {};
        const RecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
        const recognizer = new RecognitionCtor();
        recognizer.continuous = false;
        recognizer.interimResults = true;
        recognizer.maxAlternatives = 1;
        recognizer.lang = getSpeechLanguage();

        let seedText = "";
        let finalSent = false;

        recognizer.onstart = function () {
            seedText = String(textarea.value || "").trim();
            finalSent = false;
            updateMicButtonState(micBtn, true);
            if (typeof eventHooks.onStart === "function") {
                eventHooks.onStart();
            }
        };

        recognizer.onend = function () {
            updateMicButtonState(micBtn, false);
            if (typeof eventHooks.onEnd === "function") {
                eventHooks.onEnd();
            }
        };

        recognizer.onerror = function (event) {
            updateMicButtonState(micBtn, false);
            if (typeof eventHooks.onError === "function") {
                eventHooks.onError(event || {});
            }
        };

        recognizer.onresult = function (event) {
            let finalTranscript = "";
            let interimTranscript = "";

            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                const result = event.results[i];
                const segment = result && result[0] ? String(result[0].transcript || "").trim() : "";
                if (!segment) {
                    continue;
                }
                if (result.isFinal) {
                    finalTranscript += `${segment} `;
                } else {
                    interimTranscript += `${segment} `;
                }
            }

            const composedText = [seedText, (finalTranscript || interimTranscript).trim()].filter(Boolean).join(" ").trim();
            if (!composedText) {
                return;
            }

            textarea.value = composedText;
            textarea.dispatchEvent(new Event("input"));

            if (finalTranscript.trim() && !finalSent && typeof eventHooks.onFinalResult === "function") {
                finalSent = true;
                eventHooks.onFinalResult(composedText);
            }
        };

        return recognizer;
    }

    function addToHistory(role, content) {
        conversationHistory.push({ role, content });
        if (conversationHistory.length > MAX_HISTORY_TURNS * 2) {
            conversationHistory = conversationHistory.slice(-MAX_HISTORY_TURNS * 2);
        }
    }

    function normalizePreferences(rawPreferences) {
        const preferences = Object.assign({}, DEFAULT_CHAT_PREFERENCES, rawPreferences || {});

        if (!Array.isArray(preferences.answer_modes) || !preferences.answer_modes.length) {
            preferences.answer_modes = DEFAULT_CHAT_PREFERENCES.answer_modes.map(function (mode) {
                return Object.assign({}, mode);
            });
        }

        if (!preferences.answer_mode_text_block) {
            preferences.answer_mode_text_block = DEFAULT_CHAT_PREFERENCES.answer_mode_text_block;
        }

        if (!preferences.default_answer_mode) {
            preferences.default_answer_mode = DEFAULT_CHAT_PREFERENCES.default_answer_mode;
        }

        return preferences;
    }

    function setSelectedAnswerMode(mode, modeButtons, availableModes) {
        const allowedModes = (availableModes || []).length
            ? availableModes
            : DEFAULT_CHAT_PREFERENCES.answer_modes.map(function (item) {
                  return item.key;
              });

        selectedAnswerMode = allowedModes.indexOf(mode) !== -1 ? mode : DEFAULT_CHAT_PREFERENCES.default_answer_mode;
        setStoredAnswerMode(selectedAnswerMode);

        if (!modeButtons) return;
        Array.from(modeButtons.querySelectorAll(".ai-chat-mode-button")).forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.answerMode === selectedAnswerMode);
        });
    }

    // -----------------------------------------------------------------------
    // Build DOM
    // -----------------------------------------------------------------------
    const ROBOT_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.38-1 1.73V7h3a3 3 0 0 1 3 3v1h.5A1.5 1.5 0 0 1 21 12.5v3A1.5 1.5 0 0 1 19.5 17H19v1a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-1h-.5A1.5 1.5 0 0 1 3 15.5v-3A1.5 1.5 0 0 1 4.5 11H5v-1a3 3 0 0 1 3-3h3V5.73A2 2 0 0 1 10 4a2 2 0 0 1 2-2zm-3 9a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm-6 4.5h6v1H9v-1z"/>
    </svg>`;

    const SEND_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 2l4.5 4.5-1.4 1.4-2.1-2.1V16h-2V5.8L8.9 7.9 7.5 6.5 12 2zm-7 13h2v3h10v-3h2v5H5v-5z"/>
    </svg>`;

    const EXPAND_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
        <path d="M14 3h7v7h-2V6.41l-4.29 4.3-1.42-1.42L17.59 5H14V3zM10.71 13.29l1.42 1.42L7.83 19H11v2H4v-7h2v3.17l4.71-4.88z"/>
    </svg>`;

    const NEW_CHAT_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
        <path d="M12 5V2L8 6l4 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z"/>
    </svg>`;

    const MIC_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a1 1 0 1 1 2 0 7 7 0 0 1-6 6.92V21h3a1 1 0 1 1 0 2H8a1 1 0 1 1 0-2h3v-3.08A7 7 0 0 1 5 11a1 1 0 1 1 2 0 5 5 0 0 0 10 0z"/>
    </svg>`;

    const SPEAKER_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M14 3.23a1 1 0 0 1 1.68.73v16.08a1 1 0 0 1-1.68.74L8.36 16H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h4.36L14 3.23zm5.3 2.47a1 1 0 0 1 1.4 0 9 9 0 0 1 0 12.72 1 1 0 0 1-1.4-1.42 7 7 0 0 0 0-9.88 1 1 0 0 1 0-1.42zm-2.83 2.83a1 1 0 0 1 1.42 0 5 5 0 0 1 0 7.08 1 1 0 0 1-1.42-1.42 3 3 0 0 0 0-4.24 1 1 0 0 1 0-1.42z"/>
    </svg>`;

    const LIVE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 5a1 1 0 0 1 1 1v12a1 1 0 1 1-2 0V6a1 1 0 0 1 1-1zm-5 3a1 1 0 0 1 1.41 0 7 7 0 0 0 0 9.9A1 1 0 0 1 7 19.31a9 9 0 0 1 0-12.72A1 1 0 0 1 7 8zm10 0a1 1 0 0 1 1.41-1.42 9 9 0 0 1 0 12.72A1 1 0 0 1 17 17.9a7 7 0 0 0 0-9.9zM4.17 10.17a1 1 0 0 1 1.42 0 11 11 0 0 0 0 3.66 1 1 0 0 1-1.96.41 13 13 0 0 1 0-4.48 1 1 0 0 1 .54-.59zM19.83 10.17a1 1 0 0 1 .54.59 13 13 0 0 1 0 4.48 1 1 0 0 1-1.96-.41 11 11 0 0 0 0-3.66 1 1 0 0 1 1.42-1z"/>
    </svg>`;

    const STOP_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
        <path d="M8 8h8v8H8V8zm4-6a10 10 0 1 1 0 20 10 10 0 0 1 0-20z"/>
    </svg>`;

    function buildWidget() {
        const toggleBtn = document.createElement("button");
        toggleBtn.className = "ai-chat-toggle";
        toggleBtn.title = "AI Assistant";
        toggleBtn.innerHTML = ROBOT_SVG;
        toggleBtn.setAttribute("aria-label", "Open AI Assistant");

        const panel = document.createElement("div");
        panel.className = "ai-chat-panel ai-chat-hidden";
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-label", "AI Assistant Chat");

        const header = document.createElement("div");
        header.className = "ai-chat-header";
        header.innerHTML = `
            <div class="ai-chat-brand-icon">${ROBOT_SVG}</div>
            <div class="ai-chat-brand-copy">
                <div class="ai-chat-header-title" data-agent-title></div>
                <div class="ai-chat-header-subtitle" data-agent-name></div>
            </div>
            <button class="ai-chat-header-action ai-chat-header-expand" aria-label="Expand chat" title="Expand">${EXPAND_SVG}</button>
            <button class="ai-chat-header-action ai-chat-header-close" aria-label="Close chat" title="Close">&#x2715;</button>
        `;

        // Suggested actions container (above input)
        const suggestedActionsDiv = document.createElement("div");
        suggestedActionsDiv.className = "ai-chat-suggested-actions";

        const messageList = document.createElement("div");
        messageList.className = "ai-chat-messages";
        messageList.setAttribute("role", "log");
        messageList.setAttribute("aria-live", "polite");

        const emptyState = document.createElement("div");
        emptyState.className = "ai-chat-empty";
        emptyState.innerHTML = `
            <p>${DEFAULT_EMPTY_STATE_TEXT}</p>
        `;
        messageList.appendChild(emptyState);

        const inputArea = document.createElement("div");
        inputArea.className = "ai-chat-input-area";


        // Removed answer mode section


        // Create quick actions select dropdown (inline, no label, first option is 'Quick Action')
        const quickActionSelect = document.createElement("select");
        quickActionSelect.className = "ai-chat-quick-action-select";
        quickActionSelect.id = "ai-chat-quick-action-select";
        quickActionSelect.innerHTML = `<option value="">Quick Action</option>`;
        // Will be populated after preferences load

        const callPanel = document.createElement("div");
        callPanel.className = "ai-chat-call-panel";
        callPanel.innerHTML = `
            <div class="ai-chat-call-head">
                <span class="ai-chat-call-badge">Talk Mode</span>
                <span class="ai-chat-call-status" data-call-status>Ready</span>
            </div>
            <div class="ai-chat-call-transcript" data-call-transcript>${DEFAULT_CALL_TRANSCRIPT_TEXT}</div>
        `;

        const callStatusText = callPanel.querySelector("[data-call-status]");
        const callTranscript = callPanel.querySelector("[data-call-transcript]");

        const voiceStatus = document.createElement("div");
        voiceStatus.className = "ai-chat-live-status is-idle";
        voiceStatus.innerHTML = `
            <span class="ai-chat-live-dot" aria-hidden="true"></span>
            <span class="ai-chat-live-label">جاهز</span>
        `;

        const composerRow = document.createElement("div");
        composerRow.className = "ai-chat-compose";

        const textarea = document.createElement("textarea");
        textarea.className = "ai-chat-input";
        textarea.placeholder = "Write your message...";
        textarea.rows = 1;
        textarea.setAttribute("aria-label", "Message input");

        const micBtn = document.createElement("button");
        micBtn.className = "ai-chat-voice-btn ai-chat-voice-input";
        micBtn.innerHTML = MIC_SVG;
        micBtn.type = "button";
        micBtn.title = "Dictate";
        micBtn.setAttribute("aria-label", "Dictate text");
        micBtn.setAttribute("aria-pressed", "false");
        micBtn.dataset.listening = "0";

        const liveVoiceBtn = document.createElement("button");
        liveVoiceBtn.className = "ai-chat-voice-btn ai-chat-live-mode ai-chat-talk-btn";
        liveVoiceBtn.innerHTML = `${LIVE_SVG}<span class="ai-chat-talk-label">Talk</span>`;
        liveVoiceBtn.type = "button";
        liveVoiceBtn.title = "Start talk mode";
        liveVoiceBtn.setAttribute("aria-label", "Start talk mode");
        liveVoiceBtn.setAttribute("aria-pressed", "false");

        const resetBtn = document.createElement("button");
        resetBtn.className = "ai-chat-reset-btn";
        resetBtn.innerHTML = `${NEW_CHAT_SVG}<span>Reset</span>`;
        resetBtn.type = "button";
        resetBtn.title = "Reset chat";
        resetBtn.setAttribute("aria-label", "Reset chat and start new conversation");

        const voiceToggleBtn = document.createElement("button");
        voiceToggleBtn.className = "ai-chat-voice-btn ai-chat-voice-output";
        voiceToggleBtn.innerHTML = SPEAKER_SVG;
        voiceToggleBtn.type = "button";
        voiceToggleBtn.title = "Voice output off";
        voiceToggleBtn.setAttribute("aria-label", "Toggle voice output");
        voiceToggleBtn.setAttribute("aria-pressed", "false");

        const sendBtn = document.createElement("button");
        sendBtn.className = "ai-chat-send";
        sendBtn.innerHTML = SEND_SVG;
        sendBtn.title = "Send";
        sendBtn.setAttribute("aria-label", "Send message");
        sendBtn.disabled = true;

        const stopBtn = document.createElement("button");
        stopBtn.className = "ai-chat-stop";
        stopBtn.innerHTML = STOP_SVG;
        stopBtn.type = "button";
        stopBtn.title = "Stop";
        stopBtn.setAttribute("aria-label", "Stop current process");
        stopBtn.disabled = true;

        const controlsRow = document.createElement("div");
        controlsRow.className = "ai-chat-controls-row";


        const languageWrap = document.createElement("label");
        languageWrap.className = "ai-chat-language-wrap";
        languageWrap.innerHTML = `<span class="ai-chat-language-label">Language</span>`;

        const languageSelect = document.createElement("select");
        languageSelect.className = "ai-chat-language-select";
        languageSelect.setAttribute("aria-label", "Conversation language mode");
        languageSelect.innerHTML = `
            <option value="detect">Detect</option>
            <option value="ar">Arabic</option>
            <option value="en">English</option>
            <option value="zh">Chinese</option>
        `;
        languageWrap.appendChild(languageSelect);

        // Place quick actions select and language select inline on the same row
        const quickLangRow = document.createElement("div");
        quickLangRow.className = "ai-chat-quick-lang-row";
        quickLangRow.style.display = "flex";
        quickLangRow.style.gap = "8px";
        quickLangRow.style.alignItems = "center";
        quickLangRow.appendChild(quickActionSelect);
        quickLangRow.appendChild(languageWrap);

        const returnChatBtn = document.createElement("button");
        returnChatBtn.type = "button";
        returnChatBtn.className = "ai-chat-return-btn";
        returnChatBtn.textContent = "Return to chat";
        returnChatBtn.title = "Return to text chat mode";

        composerRow.appendChild(textarea);
        composerRow.appendChild(sendBtn);

        const actionRow = document.createElement("div");
        actionRow.className = "ai-chat-action-row";
        actionRow.appendChild(liveVoiceBtn);
        actionRow.appendChild(resetBtn);
        actionRow.appendChild(quickLangRow);

        controlsRow.appendChild(micBtn);
        controlsRow.appendChild(voiceToggleBtn);
        controlsRow.appendChild(voiceStatus);
        controlsRow.appendChild(returnChatBtn);

        // Removed modeSection and topicSection
        inputArea.appendChild(callPanel);
        inputArea.appendChild(suggestedActionsDiv);
        inputArea.appendChild(composerRow);
        inputArea.appendChild(actionRow);

        panel.appendChild(header);
        panel.appendChild(messageList);
        panel.appendChild(inputArea);

        document.body.appendChild(toggleBtn);
        document.body.appendChild(panel);

        return {
            toggleBtn,
            panel,
            header,
            messageList,
            emptyState,
            voiceStatus,
            callPanel,
            callStatusText,
            callTranscript,
            quickActionSelect,
            languageSelect,
            returnChatBtn,
            agentTitle: header.querySelector('[data-agent-title]'),
            agentName: header.querySelector('[data-agent-name]'),
            expandBtn: header.querySelector(".ai-chat-header-expand"),
            micBtn,
            liveVoiceBtn,
            resetBtn,
            voiceToggleBtn,
            textarea,
            sendBtn,
            stopBtn,
            suggestedActionsDiv,
        };
    }

    function removeWidget(elements) {
        if (!elements) return;
        if (elements.panel && elements.panel.parentNode) {
            elements.panel.parentNode.removeChild(elements.panel);
        }
        if (elements.toggleBtn && elements.toggleBtn.parentNode) {
            elements.toggleBtn.parentNode.removeChild(elements.toggleBtn);
        }
    }

    // -----------------------------------------------------------------------
    // Message rendering
    // -----------------------------------------------------------------------
    function normalizeMessageLink(rawValue) {
        let value = String(rawValue || "").trim();
        if (!value) {
            return "";
        }

        value = value.replace(/^[\s<>{}\[\]"'()]+|[\s<>{}\[\]"'()]+$/g, "");
        value = value.replace(/\s{2,}/g, " ").trim();
        while (/[.,;:!؟،]+$/.test(value)) {
            value = value.slice(0, -1).trim();
        }

        if (/^https?:\/\//i.test(value)) {
            return value;
        }

        if (/^(app|desk|files|api)\//i.test(value)) {
            value = `/${value}`;
        }

        if (!/^\/(app|desk|files|api)\//i.test(value)) {
            return "";
        }

        try {
            return new URL(value, window.location.origin).toString();
        } catch (e) {
            return "";
        }
    }

    function splitMessageIntoSegments(text) {
        const source = String(text || "");
        const segments = [];
        const pattern = /(https?:\/\/[^\s]+|\/?(?:app|desk|files|api)\/[^\s]+)/gi;

        let cursor = 0;
        let match;

        while ((match = pattern.exec(source)) !== null) {
            const start = match.index;
            const end = pattern.lastIndex;

            if (start > cursor) {
                segments.push({ type: "text", value: source.slice(cursor, start) });
            }

            const rawMatch = String(match[0] || "");
            const trimmed = rawMatch.trim();

            // Split trailing punctuation so it stays visible as plain text
            const trailingMatch = trimmed.match(/([.,;:!؟،)\]}]+)$/);
            const trailing = trailingMatch ? trailingMatch[1] : "";
            const candidate = trailing ? trimmed.slice(0, -trailing.length) : trimmed;

            const href = normalizeMessageLink(candidate);
            if (href) {
                let label = candidate.replace(/\s{2,}/g, " ");
                if (/^(app|desk|files|api)\//i.test(label)) {
                    label = `/${label}`;
                }
                segments.push({ type: "link", value: label, href: href });
                if (trailing) {
                    segments.push({ type: "text", value: trailing });
                }
            } else {
                segments.push({ type: "text", value: rawMatch });
            }

            cursor = end;
        }

        if (cursor < source.length) {
            segments.push({ type: "text", value: source.slice(cursor) });
        }

        if (!segments.length) {
            segments.push({ type: "text", value: source });
        }

        return segments;
    }

    function setMessageContent(container, text) {
        container.textContent = "";
        splitMessageIntoSegments(text).forEach(function (segment) {
            if (segment.type === "link") {
                const anchor = document.createElement("a");
                anchor.className = "ai-chat-link";
                anchor.href = segment.href;
                anchor.target = "_blank";
                anchor.rel = "noopener noreferrer";
                anchor.textContent = segment.value;
                anchor.title = "Open link";
                container.appendChild(anchor);
                return;
            }
            container.appendChild(document.createTextNode(segment.value));
        });
    }

    function appendMessage(messageList, emptyState, role, text) {
        if (emptyState && emptyState.parentNode) {
            emptyState.parentNode.removeChild(emptyState);
        }

        const div = document.createElement("div");
        div.className = `ai-chat-msg ${role}`;
        setMessageContent(div, text);

        const messageLanguage = languageCodeFromTag(inferLanguageFromText(text)) || getConversationLanguageCode();
        const isArabic = messageLanguage === "ar";
        div.setAttribute("lang", isArabic ? "ar" : "en");
        div.setAttribute("dir", isArabic ? "rtl" : "ltr");

        messageList.appendChild(div);
        messageList.scrollTop = messageList.scrollHeight;
        return div;
    }

    function appendInlineChips(msgDiv, inlineOptions, onChipClick) {
        if (!inlineOptions || !inlineOptions.length || !msgDiv) return;
        const chipsDiv = document.createElement("div");
        chipsDiv.className = "ai-chat-inline-chips";
        const textContent = msgDiv.textContent || "";
        const containerLang = languageCodeFromTag(inferLanguageFromText(textContent));
        const containerRtl = containerLang === "ar";
        chipsDiv.setAttribute("dir", containerRtl ? "rtl" : "ltr");
        inlineOptions.forEach(function (opt) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "ai-chat-inline-chip";
            const label = String(opt.label || opt.query || "");
            btn.textContent = label;
            const chipLang = languageCodeFromTag(inferLanguageFromText(label));
            btn.setAttribute("dir", chipLang === "ar" ? "rtl" : "ltr");
            btn.addEventListener("click", function () {
                chipsDiv.querySelectorAll(".ai-chat-inline-chip").forEach(function (c) {
                    c.disabled = true;
                });
                btn.classList.add("is-selected");
                if (typeof onChipClick === "function") {
                    onChipClick(String(opt.query || opt.label || ""));
                }
            });
            chipsDiv.appendChild(btn);
        });
        msgDiv.appendChild(chipsDiv);
    }

    function appendSuggestionLinks(msgDiv, options, onOptionClick) {
        if (!msgDiv || !Array.isArray(options) || !options.length) {
            return;
        }

        const linksDiv = document.createElement("div");
        linksDiv.className = "ai-chat-suggestion-links";

        options.slice(0, 6).forEach(function (topic) {
            const link = document.createElement("a");
            link.href = "#";
            link.className = "ai-chat-suggestion-link";
            link.textContent = String(topic.label || "").trim();
            link.title = String(topic.prompt || "").trim() || link.textContent;

            link.addEventListener("click", function (event) {
                event.preventDefault();
                if (typeof onOptionClick === "function") {
                    onOptionClick(topic);
                }
            });

            linksDiv.appendChild(link);
        });

        msgDiv.appendChild(linksDiv);
    }

    function showTyping(messageList) {
        const div = document.createElement("div");
        div.className = "ai-chat-typing";
        div.innerHTML = "<span></span><span></span><span></span>";
        messageList.appendChild(div);
        messageList.scrollTop = messageList.scrollHeight;
        return div;
    }

    // -----------------------------------------------------------------------
    // Preferences + API
    // -----------------------------------------------------------------------
    // Removed applyChatPreferences (answer mode logic)

    function loadChatPreferences(elements) {
        // Set defaults first
        // applyChatPreferences removed: all DOM updates are handled directly in loadChatPreferences

        frappe.call({
            method: "ai_assistant.api.chat.get_chat_preferences",
            callback: function (r) {
                // Debug: log backend response
                if (window.console && window.console.log) {
                    console.log("[AI Widget] get_chat_preferences response:", r);
                }
                // Agent Title
                if (elements.agentTitle) {
                    let title = (r && r.message && typeof r.message.agent_title === 'string') ? r.message.agent_title.trim() : '';
                    elements.agentTitle.textContent = title;
                }
                // Agent Name
                if (elements.agentName) {
                    let name = (r && r.message && typeof r.message.agent_name === 'string') ? r.message.agent_name.trim() : '';
                    elements.agentName.textContent = name;
                }
                // Quick Actions: only quick=1, enabled, sorted by priority/title
                if (elements.quickActionSelect) {
                    const quicks = (r && r.message && Array.isArray(r.message.quick_actions)) ? r.message.quick_actions : [];
                    elements.quickActionSelect.innerHTML = '<option value="">Quick Action</option>' +
                        quicks.map(title => `<option value="${title}">${title}</option>`).join("");
                    // Add interactive handler
                    elements.quickActionSelect.onchange = function() {
                        const val = elements.quickActionSelect.value;
                        if (val && elements.textarea) {
                            elements.textarea.value = val;
                            elements.textarea.focus();
                        }
                        // Optionally reset selection to placeholder
                        elements.quickActionSelect.selectedIndex = 0;
                    };
                }
                // Suggested Actions: only show on welcome/reset, not in chat body after conversation starts
                if (elements.suggestedActionsDiv) {
                    elements.suggestedActionsDiv.innerHTML = '';
                    const suggests = (r && r.message && Array.isArray(r.message.suggested_actions)) ? r.message.suggested_actions : [];
                    // Only show suggested actions if chat is empty (welcome/reset)
                    const chatBody = document.querySelector('.ai-chat-messages');
                    const isWelcome = chatBody && chatBody.querySelectorAll('.ai-chat-message, .ai-chat-user-message').length === 0;
                    if (isWelcome && suggests.length > 0) {
                        suggests.forEach(function(title) {
                            const btn = document.createElement('button');
                            btn.type = 'button';
                            btn.className = 'ai-chat-suggested-chip';
                            btn.textContent = title;
                            btn.onclick = function() {
                                if (elements.textarea) {
                                    elements.textarea.value = title;
                                    elements.textarea.focus();
                                }
                            };
                            elements.suggestedActionsDiv.appendChild(btn);
                        });
                    }
                }
                // Set global for other uses
                QUICK_TOPIC_OPTIONS = (r && r.message && Array.isArray(r.message.quick_actions))
                    ? r.message.quick_actions.map(title => ({ key: title, label: title, prompt: title }))
                    : [];
            },
        });
    }

    function sanitizeActionText(value) {
        const text = String(value || "")
            .replace(/[^A-Za-z0-9 _\-/\u0600-\u06FF]/g, "")
            .trim();
        return text.slice(0, 120);
    }

    function executeAssistantActions(actions) {
        if (!Array.isArray(actions) || !actions.length) {
            return 0;
        }

        if (typeof frappe === "undefined") {
            return 0;
        }

        let executedCount = 0;

        actions.forEach(function (row) {
            const action = row && typeof row === "object" ? row : {};
            const kind = String(action.action || "").toLowerCase();

            try {
                if (kind === "open_doctype" && typeof frappe.set_route === "function") {
                    const doctype = sanitizeActionText(action.doctype);
                    if (!doctype) {
                        return;
                    }
                    frappe.set_route("List", doctype);
                    executedCount += 1;
                    return;
                }

                if (kind === "open_report" && typeof frappe.set_route === "function") {
                    const reportName = sanitizeActionText(action.report_name);
                    if (!reportName) {
                        return;
                    }
                    frappe.set_route("query-report", reportName);
                    executedCount += 1;
                    return;
                }

                if (kind === "open_dashboard" && typeof frappe.set_route === "function") {
                    const dashboardName = sanitizeActionText(action.dashboard_name);
                    if (!dashboardName) {
                        return;
                    }
                    try {
                        frappe.set_route("dashboard-view", dashboardName);
                    } catch (e) {
                        frappe.set_route("dashboard", dashboardName);
                    }
                    executedCount += 1;
                    return;
                }

                if (kind === "create_report" && typeof frappe.new_doc === "function") {
                    const reportName = sanitizeActionText(action.report_name);
                    const refDoctype = sanitizeActionText(action.ref_doctype);
                    if (!reportName || !refDoctype) {
                        return;
                    }

                    frappe.route_options = Object.assign({}, frappe.route_options || {}, {
                        report_name: reportName,
                        ref_doctype: refDoctype,
                        report_type: "Report Builder",
                        is_standard: "No",
                    });
                    frappe.new_doc("Report");
                    executedCount += 1;
                    return;
                }

                if (kind === "create_dashboard" && typeof frappe.new_doc === "function") {
                    const dashboardName = sanitizeActionText(action.dashboard_name);
                    if (!dashboardName) {
                        return;
                    }

                    frappe.route_options = Object.assign({}, frappe.route_options || {}, {
                        dashboard_name: dashboardName,
                        chart_options: "Number",
                    });
                    frappe.new_doc("Dashboard");
                    executedCount += 1;
                }
            } catch (e) {
                // Ignore route execution errors to keep chat stable.
            }
        });

        return executedCount;
    }

    function sendMessage(question, messageList, emptyState, sendBtn, textarea, requestHooks) {
        if (!question.trim()) {
            return Promise.resolve({ reply: "", speechPromise: Promise.resolve() });
        }

        updateConversationLanguageFromText(question);
        rememberSpeechLanguageFromText(question);
        applyComposerLanguage(textarea);
        stopSpeaking();

        appendMessage(messageList, emptyState, "user", question);
        addToHistory("user", question);
        const userHistoryIndex = conversationHistory.length - 1;

        sendBtn.disabled = true;
        textarea.disabled = true;
        textarea.value = "";
        textarea.style.height = "";

        const typingDiv = showTyping(messageList);

        if (requestHooks && typeof requestHooks.onRequestStateChange === "function") {
            requestHooks.onRequestStateChange(true);
        }

        return new Promise(function (resolve) {
            let finished = false;
            let requestCanceled = false;
            let requestHandle = null;

            function finalize() {
                if (finished) {
                    return;
                }
                finished = true;

                if (typingDiv && typingDiv.parentNode) {
                    typingDiv.parentNode.removeChild(typingDiv);
                }

                textarea.disabled = false;
                sendBtn.disabled = false;
                textarea.focus();

                if (requestHooks && typeof requestHooks.onRequestStateChange === "function") {
                    requestHooks.onRequestStateChange(false);
                }
                if (requestHooks && typeof requestHooks.registerCancel === "function") {
                    requestHooks.registerCancel(null);
                }
            }

            function cancelRequest() {
                if (finished) {
                    return false;
                }

                requestCanceled = true;
                try {
                    if (requestHandle && typeof requestHandle.abort === "function") {
                        requestHandle.abort();
                    }
                } catch (e) {
                    // Ignore abort errors and continue local cancellation flow.
                }

                // Roll back the optimistic user turn so next prompts stay coherent.
                if (
                    userHistoryIndex >= 0 &&
                    conversationHistory[userHistoryIndex] &&
                    conversationHistory[userHistoryIndex].role === "user" &&
                    conversationHistory[userHistoryIndex].content === question
                ) {
                    conversationHistory.splice(userHistoryIndex, 1);
                }

                finalize();
                resolve({ reply: "", speechPromise: Promise.resolve(), stopped: true });
                return true;
            }

            if (requestHooks && typeof requestHooks.registerCancel === "function") {
                requestHooks.registerCancel(cancelRequest);
            }

            requestHandle = frappe.call({
                method: "ai_assistant.api.chat.send_message",
                args: {
                    question: question,
                    session_id: getSessionId(),
                    history: JSON.stringify(conversationHistory.slice(0, -1)),
                    answer_mode: selectedAnswerMode,
                    language_hint: getLanguageHintForRequest(question),
                },
                callback: function (r) {
                    if (requestCanceled) {
                        finalize();
                        return;
                    }

                    finalize();

                    if (r && r.message) {
                        const reply = r.message.reply || "No response.";
                        const msgDiv = appendMessage(messageList, null, "assistant", reply);
                        if (r.message.inline_options && r.message.inline_options.length) {
                            appendInlineChips(msgDiv, r.message.inline_options, sendQuestionWithStatus);
                        }
                        addToHistory("assistant", reply);
                        latestAssistantReply = reply;
                        if (requestHooks && typeof requestHooks.onTopicOptions === "function") {
                            requestHooks.onTopicOptions(r.message.topic_options || []);
                        }
                        executeAssistantActions(r.message.actions);
                        resolve({ reply: reply, speechPromise: speakText(reply) });
                    } else {
                        appendMessage(messageList, null, "error", "Could not get a response. Please check the AI Agent configuration.");
                        resolve({ reply: "", speechPromise: Promise.resolve() });
                    }
                },
                error: function (r) {
                    if (requestCanceled) {
                        finalize();
                        return;
                    }

                    finalize();

                    let errMsg = "An error occurred. Please try again.";
                    if (r && r.message) {
                        errMsg = r.message;
                    }
                    appendMessage(messageList, null, "error", errMsg);
                    resolve({ reply: "", speechPromise: Promise.resolve() });
                },
            });
        });
    }

    // -----------------------------------------------------------------------
    // Event wiring
    // -----------------------------------------------------------------------
    function wireEvents(elements) {
        const toggleBtn = elements.toggleBtn;
        const panel = elements.panel;
        const header = elements.header;
        const messageList = elements.messageList;
        const emptyState = elements.emptyState;
        const textarea = elements.textarea;
        const sendBtn = elements.sendBtn;
        const resetBtn = elements.resetBtn;
        const expandBtn = elements.expandBtn;
        const micBtn = elements.micBtn;
        const liveVoiceBtn = elements.liveVoiceBtn;
        const voiceToggleBtn = elements.voiceToggleBtn;
        const stopBtn = elements.stopBtn;
        const voiceStatus = elements.voiceStatus;
        const callPanel = elements.callPanel;
        const callStatusText = elements.callStatusText;
        const callTranscript = elements.callTranscript;
        const quickTopicChips = elements.quickTopicChips;
        const languageSelect = elements.languageSelect;
        const returnChatBtn = elements.returnChatBtn;
        const voiceStatusLabel = voiceStatus.querySelector(".ai-chat-live-label");

        const STATUS_LABELS = {
            idle: "Ready | جاهز",
            listening: "Listening | يستمع",
            processing: "Processing | يعالج",
            speaking: "Speaking | يتحدث",
        };

        let activeVoiceIntent = null; // "dictate" | "live" | null
        let liveVoiceModeEnabled = false;
        let preferServerTranscription = false;
        let stopServerRecording = null;
        let serverFlowActive = false;
        let requestInFlight = false;
        let cancelInFlightRequest = null;
        let activeQuickTopicOptions = rankTopicOptionsByInterest(mergeQuickTopicOptions([]));
        const canServerCapture = supportsServerAudioCapture();
        let speechLanguageCandidates = [];
        let speechLanguageIndex = 0;

        let isOpen = false;

        function refreshStopButtonState() {
            if (!stopBtn) {
                return;
            }

            const speaking = supportsSpeechSynthesis()
                ? Boolean(window.speechSynthesis && (window.speechSynthesis.speaking || window.speechSynthesis.pending))
                : false;

            const hasActiveProcess = Boolean(
                requestInFlight || cancelInFlightRequest || activeVoiceIntent || serverFlowActive || liveVoiceModeEnabled || speaking
            );

            stopBtn.disabled = !hasActiveProcess;
            stopBtn.classList.toggle("is-active", hasActiveProcess);
        }

        function setVoiceStatus(state) {
            const normalized = Object.prototype.hasOwnProperty.call(STATUS_LABELS, state) ? state : "idle";
            voiceStatus.classList.remove("is-idle", "is-listening", "is-processing", "is-speaking");
            voiceStatus.classList.add(`is-${normalized}`);
            voiceStatusLabel.textContent = STATUS_LABELS[normalized];
            if (callStatusText) {
                callStatusText.textContent = STATUS_LABELS[normalized];
            }
        }

        function retryVoiceCaptureWithNextLanguage(intent) {
            if (!voiceRecognizer) {
                return false;
            }

            if (speechLanguageIndex >= speechLanguageCandidates.length - 1) {
                return false;
            }

            speechLanguageIndex += 1;

            try {
                activeVoiceIntent = intent || "dictate";
                setVoiceStatus("listening");
                voiceRecognizer.lang = speechLanguageCandidates[speechLanguageIndex];
                voiceRecognizer.start();
                return true;
            } catch (e) {
                activeVoiceIntent = null;
                return false;
            }
        }

        function showVoiceError(message) {
            appendMessage(messageList, null, "error", message);
        }

        function getStarterGreetingText() {
            const languageCode = getConversationLanguageCode();
            const userName = getUserDisplayName();

            if (languageCode === "ar") {
                return `مرحباً ${userName}. اختر موضوعاً مقترحاً أو اكتب سؤالك مباشرة.`;
            }

            if (languageCode === "zh") {
                return `你好 ${userName}。请选择建议主题，或直接输入你的问题。`;
            }

            return `Hello ${userName}. Pick a suggested subject or type your question.`;
        }

        function renderStarterGreeting() {
            const greetingDiv = appendMessage(messageList, emptyState, "assistant", getStarterGreetingText());
            const starterTopics = rankTopicOptionsByInterest(activeQuickTopicOptions).slice(0, 5);
            appendSuggestionLinks(greetingDiv, starterTopics, handleQuickTopicSelection);
        }

        function resetConversation() {
            if (typeof cancelInFlightRequest === "function") {
                cancelInFlightRequest();
            }

            setLiveVoiceMode(false);
            stopAllVoiceCapture();
            stopSpeaking();

            conversationHistory = [];
            latestAssistantReply = "";
            startNewSessionId();

            activeQuickTopicOptions = rankTopicOptionsByInterest(mergeQuickTopicOptions([]));
            renderQuickTopicOptions(quickTopicChips, activeQuickTopicOptions, handleQuickTopicSelection);

            messageList.innerHTML = "";
            renderStarterGreeting();

            if (callTranscript) {
                callTranscript.textContent = DEFAULT_CALL_TRANSCRIPT_TEXT;
            }

            textarea.disabled = false;
            textarea.value = "";
            textarea.style.height = "";
            applyComposerLanguage(textarea);
            sendBtn.disabled = true;

            setVoiceStatus("idle");
            refreshStopButtonState();

            if (isOpen) {
                textarea.focus();
            }
        }

        function resetConversationWithReopen() {
            closePanel();
            window.setTimeout(function () {
                resetConversation();
                openPanel();
            }, 100);
        }

        function stopAllVoiceCapture() {
            if (activeVoiceIntent && voiceRecognizer) {
                voiceRecognizer.stop();
            }
            activeVoiceIntent = null;

            if (typeof stopServerRecording === "function") {
                stopServerRecording();
            }
            stopServerRecording = null;
            serverFlowActive = false;
            updateMicButtonState(micBtn, false);
            setVoiceStatus("idle");
            refreshStopButtonState();
        }

        function setLiveVoiceMode(enabled) {
            liveVoiceModeEnabled = Boolean(enabled);
            updateLiveVoiceButtonState(liveVoiceBtn, liveVoiceModeEnabled);
            panel.classList.toggle("ai-chat-live-active", liveVoiceModeEnabled);
            callPanel.classList.toggle("is-active", liveVoiceModeEnabled);
            returnChatBtn.classList.toggle("is-visible", liveVoiceModeEnabled);

            textarea.disabled = liveVoiceModeEnabled;
            if (liveVoiceModeEnabled) {
                sendBtn.disabled = true;
                textarea.blur();
                liveVoiceBtn.title = "Stop talk mode";
                liveVoiceBtn.setAttribute("aria-label", "Stop talk mode");
            } else {
                liveVoiceBtn.title = "Start talk mode";
                liveVoiceBtn.setAttribute("aria-label", "Start talk mode");
            }

            if (!liveVoiceModeEnabled) {
                stopAllVoiceCapture();
                setVoiceStatus("idle");
                textarea.disabled = false;
                sendBtn.disabled = !textarea.value.trim();
                callTranscript.textContent = DEFAULT_CALL_TRANSCRIPT_TEXT;
                refreshStopButtonState();
                return;
            }

            if (!voiceOutputEnabled) {
                voiceOutputEnabled = true;
                setStoredVoiceOutputEnabled(true);
                updateVoiceOutputButtonState(voiceToggleBtn);
            }

            if (!isOpen) {
                openPanel();
            }

            setVoiceStatus("listening");
            refreshStopButtonState();

            window.setTimeout(function () {
                startVoiceCapture("live");
            }, 120);
        }

        async function sendQuestionWithStatus(question, intent) {
            learnUserInterestsFromQuestion(question);
            activeQuickTopicOptions = rankTopicOptionsByInterest(activeQuickTopicOptions);
            renderQuickTopicOptions(quickTopicChips, activeQuickTopicOptions, handleQuickTopicSelection);

            setVoiceStatus("processing");
            if (intent === "live") {
                callTranscript.textContent = `You: ${question}`;
            }
            const result = await sendMessage(question, messageList, emptyState, sendBtn, textarea, {
                onRequestStateChange: function (inFlight) {
                    requestInFlight = Boolean(inFlight);
                    refreshStopButtonState();
                },
                registerCancel: function (cancelFn) {
                    cancelInFlightRequest = typeof cancelFn === "function" ? cancelFn : null;
                    refreshStopButtonState();
                },
                onTopicOptions: function (topicOptions) {
                    activeQuickTopicOptions = rankTopicOptionsByInterest(mergeQuickTopicOptions(topicOptions));
                    renderQuickTopicOptions(quickTopicChips, activeQuickTopicOptions, handleQuickTopicSelection);
                },
            });

            if (result && result.stopped) {
                textarea.disabled = false;
                sendBtn.disabled = !textarea.value.trim();
                setVoiceStatus(liveVoiceModeEnabled && isOpen ? "listening" : "idle");
                refreshStopButtonState();
                return;
            }

            const hasReply = Boolean(result && String(result.reply || "").trim());
            if (hasReply && voiceOutputEnabled) {
                setVoiceStatus("speaking");
                if (intent === "live") {
                    callTranscript.textContent = `Assistant: ${String(result.reply || "").trim()}`;
                }
                await result.speechPromise;
            } else if (hasReply && intent === "live") {
                callTranscript.textContent = `Assistant: ${String(result.reply || "").trim()}`;
            }

            if (intent === "live" && liveVoiceModeEnabled && isOpen) {
                textarea.disabled = true;
                sendBtn.disabled = true;
                setVoiceStatus("listening");
                window.setTimeout(function () {
                refreshStopButtonState();
                    startVoiceCapture("live");
                }, 300);
                return;
            }

            textarea.disabled = false;
            sendBtn.disabled = !textarea.value.trim();
            setVoiceStatus("idle");
        }

        async function handleRecognizedText(spokenText, intent) {
            const cleanText = String(spokenText || "").trim();
            if (!cleanText) {
                if (intent === "live" && liveVoiceModeEnabled && isOpen) {
                    setVoiceStatus("listening");
                    window.setTimeout(function () {
                        startVoiceCapture("live");
                    }, 280);
                } else {
                    setVoiceStatus("idle");
                }
                return;
            }

            rememberSpeechLanguageFromText(cleanText);
            if (intent === "live") {
                callTranscript.textContent = `You: ${cleanText}`;
            }

            textarea.value = cleanText;
            textarea.dispatchEvent(new Event("input"));

            if (intent !== "live") {
                textarea.focus();
                setVoiceStatus("idle");
                return;
            }

            await sendQuestionWithStatus(cleanText, "live");
        }

        async function runServerVoiceFlow(intent) {
            if (!canServerCapture) {
                showVoiceError("المتصفح لا يدعم التسجيل الصوتي في هذه الصفحة.");
                return;
            }

            if (window.isSecureContext === false) {
                showVoiceError("ميزة الصوت تتطلب اتصالًا آمنًا HTTPS.");
                return;
            }

            if (serverFlowActive) {
                if (typeof stopServerRecording === "function") {
                    stopServerRecording();
                }
                return;
            }

            const runIntent = intent || "dictate";

            serverFlowActive = true;
            updateMicButtonState(micBtn, true);
            setVoiceStatus("listening");
            micBtn.title = runIntent === "live" ? "Listening..." : "Voice typing...";

            try {
                const recorded = await recordAudioClip(SERVER_RECORDING_MAX_MS, function (stopFn) {
                    stopServerRecording = stopFn;
                });
                stopServerRecording = null;

                setVoiceStatus("processing");
                micBtn.title = "جاري تحويل الصوت إلى نص...";
                const spokenText = await transcribeAudioOnServer(
                    recorded.audioBase64,
                    recorded.mimeType,
                    getConversationLanguageCode()
                );
                await handleRecognizedText(spokenText, runIntent);
            } catch (error) {
                const raw = String(error && error.message ? error.message : "").trim();
                const knownCode = [
                    "capture-not-supported",
                    "mic-permission",
                    "recorder-init",
                    "recording-error",
                    "empty-audio",
                    "blob-convert",
                ].indexOf(raw) !== -1;
                showVoiceError(knownCode ? serverTranscriptionErrorMessage(raw) : (raw || serverTranscriptionErrorMessage("")));

                if (raw === "mic-permission") {
                    setLiveVoiceMode(false);
                    return;
                }

                if (runIntent === "live" && liveVoiceModeEnabled && isOpen) {
                    setVoiceStatus("listening");
                    window.setTimeout(function () {
                        startVoiceCapture("live");
                    }, 650);
                } else {
                    setVoiceStatus("idle");
                }
            } finally {
                stopServerRecording = null;
                serverFlowActive = false;
                updateMicButtonState(micBtn, false);
                micBtn.title = "Voice typing";
            }
        }

        function startVoiceCapture(intent, preserveCandidates) {
            if (!isOpen) {
                return;
            }

            if (window.isSecureContext === false) {
                showVoiceError("ميزة الصوت تتطلب اتصالًا آمنًا HTTPS.");
                if (intent === "live") {
                    setLiveVoiceMode(false);
                } else {
                    setVoiceStatus("idle");
                }
                return;
            }

            if (serverFlowActive || activeVoiceIntent) {
                return;
            }

            const runIntent = intent || "dictate";
            if (preferServerTranscription || !voiceRecognizer) {
                runServerVoiceFlow(runIntent);
                return;
            }

            if (!preserveCandidates) {
                speechLanguageCandidates = getSpeechLanguageCandidates(textarea.value);
                speechLanguageIndex = 0;
            }

            const recognitionLanguage = speechLanguageCandidates[speechLanguageIndex] || getSpeechLanguage();

            try {
                activeVoiceIntent = runIntent;
                setVoiceStatus("listening");
                voiceRecognizer.lang = recognitionLanguage;
                voiceRecognizer.start();
                refreshStopButtonState();
            } catch (e) {
                activeVoiceIntent = null;
                if (canServerCapture) {
                    preferServerTranscription = true;
                    runServerVoiceFlow(runIntent);
                    return;
                }
                showVoiceError("تعذر تشغيل التعرف على الصوت في هذا المتصفح.");
                setVoiceStatus("idle");
                refreshStopButtonState();
            }
        }

        voiceOutputEnabled = getStoredVoiceOutputEnabled();
        updateVoiceOutputButtonState(voiceToggleBtn);
        conversationLanguageMode = getStoredLanguageMode();
        if (conversationLanguageMode === "ar" || conversationLanguageMode === "en" || conversationLanguageMode === "zh") {
            conversationLanguageHint = conversationLanguageMode;
        }
        languageSelect.value = conversationLanguageMode;

        const voiceRecognizer = createSpeechRecognizer(textarea, micBtn, {
            onStart: function () {
                micBtn.title = activeVoiceIntent === "live" ? "Live listening..." : "Voice typing...";
                setVoiceStatus("listening");
            },
            onEnd: function () {
                activeVoiceIntent = null;
                micBtn.title = "Voice typing";

                if (!liveVoiceModeEnabled && !serverFlowActive) {
                    setVoiceStatus("idle");
                }
            },
            onError: function (event) {
                const code = String(event && event.error ? event.error : "");
                const failedIntent = activeVoiceIntent || "dictate";
                activeVoiceIntent = null;

                if ((code === "no-speech" || code === "language-not-supported") && retryVoiceCaptureWithNextLanguage(failedIntent)) {
                    return;
                }

                if (code === "no-speech" && failedIntent === "live" && liveVoiceModeEnabled && isOpen) {
                    setVoiceStatus("listening");
                    window.setTimeout(function () {
                        startVoiceCapture("live");
                    }, 220);
                    return;
                }

                if (code === "network" && canServerCapture) {
                    preferServerTranscription = true;
                    setVoiceStatus("processing");
                    window.setTimeout(function () {
                        runServerVoiceFlow(failedIntent);
                    }, 100);
                    return;
                }
                showVoiceError(speechErrorMessage(code));

                if (failedIntent === "live" && liveVoiceModeEnabled && isOpen) {
                    setVoiceStatus("listening");
                    window.setTimeout(function () {
                        startVoiceCapture("live");
                    }, 650);
                } else {
                    setVoiceStatus("idle");
                }
            },
            onFinalResult: function (spokenText) {
                const intent = activeVoiceIntent || "dictate";
                activeVoiceIntent = null;
                handleRecognizedText(spokenText, intent);
            },
        });

        if (!voiceRecognizer && !canServerCapture) {
            micBtn.disabled = true;
            liveVoiceBtn.disabled = true;
            micBtn.title = "Voice input is not supported in this browser";
        } else if (!voiceRecognizer && canServerCapture) {
            preferServerTranscription = true;
            micBtn.title = "Voice typing";
        }

        if (!supportsSpeechSynthesis()) {
            voiceToggleBtn.disabled = true;
            voiceToggleBtn.title = "Voice output is not supported in this browser";
        }

        function handleQuickTopicSelection(selected) {
            const quickPrompt = String((selected && selected.prompt) || "").trim();
            if (!quickPrompt || requestInFlight || textarea.disabled) {
                return;
            }

            if (selected && selected.key) {
                increaseTopicInterest(selected.key, 2);
            }
            learnUserInterestsFromQuestion(quickPrompt);

            textarea.value = quickPrompt;
            textarea.dispatchEvent(new Event("input"));
            sendQuestionWithStatus(quickPrompt, "manual");
        }

        renderQuickTopicOptions(quickTopicChips, activeQuickTopicOptions, handleQuickTopicSelection);
        setConversationLanguageMode(conversationLanguageMode, textarea, languageSelect);
        applyComposerLanguage(textarea);
        setVoiceStatus("idle");
        refreshStopButtonState();
        resetConversation();

        let _placeholderInterval = null;

        function setRandomTopicPlaceholder() {
            if (String(textarea.value || "").trim()) return;
            const topics = activeQuickTopicOptions.length ? activeQuickTopicOptions : QUICK_TOPIC_OPTIONS;
            if (!topics.length) return;
            const pick = topics[Math.floor(Math.random() * topics.length)];
            if (pick && pick.label) {
                textarea.placeholder = pick.label;
            }
        }

        function startPlaceholderRotation() {
            setRandomTopicPlaceholder();
            if (_placeholderInterval) return;
            _placeholderInterval = setInterval(function () {
                if (!isOpen || document.activeElement === textarea || String(textarea.value || "").trim()) return;
                setRandomTopicPlaceholder();
            }, 3500);
        }

        function stopPlaceholderRotation() {
            clearInterval(_placeholderInterval);
            _placeholderInterval = null;
        }

        function openPanel() {
            isOpen = true;
            panel.classList.remove("ai-chat-hidden");
            toggleBtn.setAttribute("aria-expanded", "true");
            languageSelect.value = conversationLanguageMode;
            applyComposerLanguage(textarea);
            startPlaceholderRotation();
            if (!liveVoiceModeEnabled) {
                textarea.focus();
            }
        }

        function closePanel() {
            isOpen = false;
            panel.classList.add("ai-chat-hidden");
            toggleBtn.setAttribute("aria-expanded", "false");

            setLiveVoiceMode(false);
            stopPlaceholderRotation();
            stopSpeaking();
            refreshStopButtonState();
        }

        toggleBtn.addEventListener("click", function () {
            if (isOpen) {
                closePanel();
            } else {
                openPanel();
            }
        });

        header.querySelector(".ai-chat-header-close").addEventListener("click", closePanel);
        if (resetBtn) {
            resetBtn.addEventListener("click", function () {
                resetConversationWithReopen();
            });
        }
        expandBtn.addEventListener("click", function () {
            panel.classList.toggle("ai-chat-expanded");
        });

        liveVoiceBtn.addEventListener("click", function () {
            if (liveVoiceBtn.disabled) {
                return;
            }
            setLiveVoiceMode(!liveVoiceModeEnabled);
        });

        returnChatBtn.addEventListener("click", function () {
            if (liveVoiceModeEnabled) {
                setLiveVoiceMode(false);
            }
            textarea.focus();
        });

        languageSelect.addEventListener("change", function () {
            setConversationLanguageMode(this.value, textarea, this);
        });

        micBtn.addEventListener("click", function () {
            if (micBtn.disabled) {
                return;
            }

            if (serverFlowActive || activeVoiceIntent) {
                stopAllVoiceCapture();
                return;
            }

            if (liveVoiceModeEnabled) {
                setLiveVoiceMode(false);
            }

            startVoiceCapture("dictate");
        });

        stopBtn.addEventListener("click", function () {
            let stoppedAnything = false;

            if (typeof cancelInFlightRequest === "function") {
                stoppedAnything = cancelInFlightRequest() || stoppedAnything;
            }

            if (liveVoiceModeEnabled) {
                setLiveVoiceMode(false);
                stoppedAnything = true;
            } else if (serverFlowActive || activeVoiceIntent) {
                stopAllVoiceCapture();
                stoppedAnything = true;
            }

            if (supportsSpeechSynthesis() && window.speechSynthesis) {
                if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
                    stoppedAnything = true;
                }
            }

            stopSpeaking();
            setVoiceStatus("idle");
            refreshStopButtonState();

            if (stoppedAnything) {
                appendMessage(messageList, null, "assistant", "Process stopped.");
            }
        });

        voiceToggleBtn.addEventListener("click", function () {
            if (!supportsSpeechSynthesis()) {
                return;
            }

            voiceOutputEnabled = !voiceOutputEnabled;
            setStoredVoiceOutputEnabled(voiceOutputEnabled);
            updateVoiceOutputButtonState(voiceToggleBtn);

            if (voiceOutputEnabled && latestAssistantReply) {
                setVoiceStatus("speaking");
                speakText(latestAssistantReply).finally(function () {
                    if (liveVoiceModeEnabled && isOpen) {
                        setVoiceStatus("listening");
                        refreshStopButtonState();
                        return;
                    }
                    setVoiceStatus("idle");
                    refreshStopButtonState();
                });
            } else {
                stopSpeaking();
                if (liveVoiceModeEnabled && isOpen) {
                    setVoiceStatus("listening");
                } else {
                    setVoiceStatus("idle");
                }
                refreshStopButtonState();
            }
        });

        textarea.addEventListener("input", function () {
            updateConversationLanguageFromText(this.value);
            applyComposerLanguage(this);
            this.style.height = "auto";
            this.style.height = Math.min(this.scrollHeight, 120) + "px";
            sendBtn.disabled = !this.value.trim();
        });

        sendBtn.addEventListener("click", function () {
            const q = textarea.value;
            if (q.trim()) {
                sendQuestionWithStatus(q, "manual");
            }
        });

        textarea.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                const q = this.value;
                if (q.trim()) {
                    sendQuestionWithStatus(q, "manual");
                }
            }
        });
    }

    // -----------------------------------------------------------------------
    // Init
    // -----------------------------------------------------------------------
    function init() {
        if (widgetBlockedByRole || document.querySelector(".ai-chat-toggle")) return;

        if (!frappe.session || !frappe.session.user) {
            if (initRetryCount < MAX_INIT_RETRIES) {
                initRetryCount += 1;
                window.setTimeout(init, 500);
            }
            return;
        }

        if (frappe.session.user === "Guest") return;

        frappe.call({
            method: "ai_assistant.api.chat.get_chat_preferences",
            callback: function (r) {
                const message = r && r.message ? r.message : {};
                const widgetEnabled = Boolean(message.widget_enabled);

                if (!widgetEnabled) {
                    widgetBlockedByRole = true;
                    return;
                }

                if (document.querySelector(".ai-chat-toggle")) {
                    return;
                }

                const elements = buildWidget();
                wireEvents(elements);
                loadChatPreferences(elements);
            },
            error: function () {
                // Safe fallback: do not render widget when access check fails.
                widgetBlockedByRole = true;
            },
        });
    }

    function onDocumentReady(callback) {
        if (typeof frappe !== "undefined" && typeof frappe.ready === "function") {
            frappe.ready(callback);
            return;
        }

        if (typeof document !== "undefined") {
            if (document.readyState === "loading") {
                document.addEventListener("DOMContentLoaded", callback, { once: true });
            } else {
                callback();
            }
        }
    }

    onDocumentReady(init);

    if (typeof $ === "function") {
        $(document).on("app:navigate page:navigate", function () {
            init();
        });
    } else {
        window.addEventListener("hashchange", init);
    }
})();
