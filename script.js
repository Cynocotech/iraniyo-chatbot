// YouTube links loaded from videos.json at runtime
let YOUTUBE_VIDEOS = [];
fetch('videos.json')
    .then(r => r.json())
    .then(d => { YOUTUBE_VIDEOS = d.videos || []; })
    .catch(() => {});

// Number of questions allowed before the ad overlay appears (admin-configurable)
let AD_THRESHOLD = 5;
const adThresholdNotice = document.getElementById('adThresholdNotice');
function updateAdThresholdNotice() {
    if (!adThresholdNotice) return;
    adThresholdNotice.textContent = `با استفاده از این دستیار، شما می‌پذیرید که جهت تأمین هزینه‌های سرور، پس از هر ${AD_THRESHOLD} سوال یک تبلیغ مشاهده کنید. از حمایت شما سپاسگزاریم.`;
}
updateAdThresholdNotice();
fetch('ads-config.json')
    .then(r => r.json())
    .then(d => {
        if (d.threshold) AD_THRESHOLD = d.threshold;
        updateAdThresholdNotice();
    })
    .catch(() => {});

const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const quickQuestions = document.getElementById('quickQuestions');

// Pre-chat elements
const preChatContainer = document.getElementById('preChatContainer');
const chatInterface = document.getElementById('chatInterface');
const startChatForm = document.getElementById('startChatForm');
const userNameInput = document.getElementById('userName');
const userEmailInput = document.getElementById('userEmail');

// Ad Overlay elements
const adOverlay = document.getElementById('adOverlay');
const skipAdBtn = document.getElementById('skipAdBtn');
let questionCount = parseInt(localStorage.getItem('n8n_chat_qcount') || '0');
let pendingMessage = '';

// Configure marked to handle line breaks like standard Markdown
marked.setOptions({
    breaks: true,
    gfm: true
});

// All chat requests go through the server-side proxy to the Python (FastAPI) backend
const CHAT_URL       = 'proxy.php';
const AGENTS_URL     = 'agents.php';
const TRANSCRIPT_URL = 'transcript.php';

const notificationSound = new Audio('https://assets.mixkit.co/active_storage/sfx/2354/2354-preview.mp3');

const chatInputContainer = document.getElementById('chatInputContainer');
const quickQuestionsWrapper = document.getElementById('quickQuestionsWrapper');
const agentTabs = document.getElementById('agentTabs');
const agentResetBtn = document.getElementById('agentResetBtn');
const endChatBtn = document.getElementById('endChatBtn');
const micBtn = document.getElementById('micBtn');
const voiceToggleBtn = document.getElementById('voiceToggleBtn');

// User info handling
let userInfo = JSON.parse(localStorage.getItem('n8n_chat_user'));

// ── Agent state ─────────────────────────────────────────────
const AGENT_KEY = 'iraniyo_agent';
const AGENTS_WITH_RESET = ['trip-planner'];
const HISTORY_EXPIRY_MS = 5 * 24 * 60 * 60 * 1000; // 5 days
const MAX_STORED = 60;

let AGENTS_META  = {};
let currentAgent = localStorage.getItem(AGENT_KEY) || 'dr-yas';
let thinking = false;

function sessionKey(agent) { return `iraniyo_session_${agent}`; }
function historyKey(agent) { return `iraniyo_history_${agent}`; }

function getSessionId(agent) {
    return localStorage.getItem(sessionKey(agent)) || '';
}
function setSessionId(agent, id) {
    localStorage.setItem(sessionKey(agent), id);
}

function loadStoredMessages(agent) {
    try {
        const raw = JSON.parse(localStorage.getItem(historyKey(agent)) || 'null');
        if (!raw) return [];
        if (raw.ts && Date.now() - raw.ts > HISTORY_EXPIRY_MS) {
            localStorage.removeItem(historyKey(agent));
            localStorage.removeItem(sessionKey(agent));
            return [];
        }
        return raw.messages || [];
    } catch { return []; }
}
function saveStoredMessages(agent, msgs) {
    if (msgs.length > MAX_STORED) msgs = msgs.slice(-MAX_STORED);
    localStorage.setItem(historyKey(agent), JSON.stringify({ ts: Date.now(), messages: msgs }));
}
function appendStoredMessage(agent, role, text, html) {
    const msgs = loadStoredMessages(agent);
    msgs.push({ role, text, html });
    saveStoredMessages(agent, msgs);
}

// Build Gemini-format history ({role:'user'|'assistant', content}) for
// agents that manage their own conversation state client-side (trip planner)
function buildClientHistory(agent) {
    return loadStoredMessages(agent).map(m => ({
        role: m.role === 'bot' ? 'assistant' : 'user',
        content: m.text || ''
    }));
}

// ── Agents metadata (icons, names, welcome messages, chips) ─
async function loadAgentsMeta() {
    try {
        const res = await fetch(AGENTS_URL);
        const list = await res.json();
        AGENTS_META = {};
        (list || []).forEach(a => { AGENTS_META[a.slug] = a; });
        syncAgentTabs();
    } catch (e) {
        console.error('Failed to load agents:', e);
    }
}

function syncAgentTabs() {
    agentTabs.querySelectorAll('.agent-tab').forEach(btn => {
        const meta = AGENTS_META[btn.dataset.agent];
        if (!meta) return;
        const iconEl = btn.querySelector('.agent-tab-icon');
        const labelEl = btn.querySelector('.agent-tab-label');
        if (iconEl) iconEl.textContent = meta.icon;
        if (labelEl) labelEl.textContent = meta.name;
    });
}

// ── Pre-chat / chat visibility ───────────────────────────────
function showChat() {
    preChatContainer.style.display = 'none';
    chatInterface.style.display = 'flex';
    chatInputContainer.style.display = 'block';
    quickQuestionsWrapper.style.display = 'block';
}

function showPreChat() {
    preChatContainer.style.display = 'flex';
    chatInterface.style.display = 'none';
    chatInputContainer.style.display = 'none';
    quickQuestionsWrapper.style.display = 'none';
}

if (userInfo && userInfo.name && userInfo.email) {
    showChat();
} else {
    showPreChat();
}

startChatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const name = userNameInput.value.trim();
    const email = userEmailInput.value.trim();

    if (name && email) {
        userInfo = { name, email };
        localStorage.setItem('n8n_chat_user', JSON.stringify(userInfo));
        // Save lead to CSV server-side
        fetch('save_user.php', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, agent_slug: currentAgent })
        }).catch(() => {});
        showChat();
        renderAgent(currentAgent);
    }
});

// Auto-resize textarea
chatInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
    sendBtn.disabled = this.value.trim() === '';
});

chatInput.addEventListener('keydown', function (e) {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (this.value.trim() !== '') {
            chatForm.requestSubmit();
        }
    }
});

// ── Rendering helpers ────────────────────────────────────────
function escapeHtml(t) {
    return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderMessageDOM(html, sender) {
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message', sender);

    const avatarDiv = document.createElement('div');
    avatarDiv.classList.add('msg-avatar');
    if (sender === 'bot') {
        avatarDiv.innerHTML = AGENTS_META[currentAgent]?.icon || '<i class="fa-solid fa-robot"></i>';
    } else {
        avatarDiv.innerHTML = '<i class="fa-solid fa-user"></i>';
    }

    const contentDiv = document.createElement('div');
    contentDiv.classList.add('message-content');
    contentDiv.innerHTML = html;

    msgDiv.appendChild(avatarDiv);
    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    return msgDiv;
}

// Render + (optionally) persist a NEW message (user input or bot reply)
function addMessage(content, sender, persist = true) {
    let html, text;
    if (sender === 'bot') {
        const rendered = typeof marked !== 'undefined' ? marked.parse(content) : content.replace(/\n/g, '<br>');
        html = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(rendered) : rendered;
        text = content.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    } else {
        text = content;
        html = escapeHtml(content);
    }
    renderMessageDOM(html, sender);
    scrollToBottom();
    if (persist) appendStoredMessage(currentAgent, sender, text, html);
    if (sender === 'bot' && persist) speak(text);
}

function addTypingIndicator() {
    const indicator = document.createElement('div');
    indicator.classList.add('typing-indicator', 'active', 'message', 'bot');
    const icon = AGENTS_META[currentAgent]?.icon || '<i class="fa-solid fa-robot"></i>';
    indicator.innerHTML = `
        <div class="msg-avatar">${icon}</div>
        <div class="message-content" style="display: flex; align-items: center; gap: 8px;">
            <i class="fa-solid fa-circle-notch fa-spin" style="color: var(--primary-hover); font-size: 1.1rem;"></i>
            <span style="font-size: 0.9rem; color: var(--text-main);">در حال بررسی و جستجو...</span>
        </div>
    `;
    chatMessages.appendChild(indicator);
    scrollToBottom();
    return indicator;
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── Suggestion chips ─────────────────────────────────────────
function renderChips(meta) {
    quickQuestions.innerHTML = '';
    (meta?.chips || []).forEach(chip => {
        const btn = document.createElement('button');
        btn.className = 'quick-btn';
        btn.type = 'button';
        btn.textContent = chip;
        btn.addEventListener('click', () => {
            if (thinking) return;
            const clean = chip.replace(/^[\p{Emoji}\s]+/u, '').trim() || chip.trim();
            chatInput.value = clean;
            chatInput.style.height = 'auto';
            sendBtn.disabled = false;
            chatForm.requestSubmit();
        });
        quickQuestions.appendChild(btn);
    });
}

// ── Agent rendering / switching ──────────────────────────────
function renderAgent(agent) {
    chatMessages.innerHTML = '';
    const meta = AGENTS_META[agent];
    const stored = loadStoredMessages(agent);

    if (stored.length) {
        stored.forEach(m => renderMessageDOM(m.html, m.role));
    } else if (meta) {
        const greet = userInfo?.name ? `سلام ${userInfo.name}! ` : '';
        addMessage(greet + meta.welcome_message, 'bot', false);
    } else {
        addMessage('⚠️ اتصال به دستیار برقرار نشد. لطفاً صفحه را تازه‌سازی کنید.', 'bot', false);
    }

    renderChips(meta);
    agentTabs.classList.toggle('show-reset', AGENTS_WITH_RESET.includes(agent));
    scrollToBottom();
}

agentTabs.querySelectorAll('.agent-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        if (thinking) return;
        const agent = btn.dataset.agent;
        if (agent === currentAgent) return;
        cancelSpeech();
        currentAgent = agent;
        localStorage.setItem(AGENT_KEY, agent);
        agentTabs.querySelectorAll('.agent-tab').forEach(b => b.classList.toggle('active', b.dataset.agent === agent));
        if (chatInterface.style.display !== 'none') renderAgent(agent);
        chatInput.focus();
    });
});

// ── Themed confirm / info modal (replaces native confirm()/alert()) ──
const confirmModal = document.getElementById('confirmModal');
const confirmIcon = document.getElementById('confirmIcon');
const confirmTitle = document.getElementById('confirmTitle');
const confirmMessage = document.getElementById('confirmMessage');
const confirmOkBtn = document.getElementById('confirmOkBtn');
const confirmCancelBtn = document.getElementById('confirmCancelBtn');

function showModal({ icon = 'fa-circle-info', title, message, okLabel = 'باشه', cancelLabel = null }) {
    return new Promise((resolve) => {
        confirmIcon.innerHTML = `<i class="fa-solid ${icon}"></i>`;
        confirmTitle.textContent = title;
        confirmMessage.textContent = message;
        confirmOkBtn.textContent = okLabel;
        confirmCancelBtn.style.display = cancelLabel ? '' : 'none';
        confirmCancelBtn.textContent = cancelLabel || '';
        confirmModal.style.display = 'flex';

        function close(result) {
            confirmModal.style.display = 'none';
            confirmOkBtn.removeEventListener('click', onOk);
            confirmCancelBtn.removeEventListener('click', onCancel);
            resolve(result);
        }
        function onOk() { close(true); }
        function onCancel() { close(false); }
        confirmOkBtn.addEventListener('click', onOk);
        confirmCancelBtn.addEventListener('click', onCancel);
    });
}

agentResetBtn.addEventListener('click', async () => {
    if (thinking) return;
    const ok = await showModal({
        icon: 'fa-rotate-right',
        title: 'شروع مجدد گفتگو',
        message: 'گفتگو را از ابتدا شروع کنیم؟',
        okLabel: 'بله، شروع مجدد',
        cancelLabel: 'انصراف',
    });
    if (!ok) return;
    cancelSpeech();
    localStorage.removeItem(historyKey(currentAgent));
    localStorage.removeItem(sessionKey(currentAgent));
    localStorage.removeItem(sentTranscriptKey(currentAgent));
    renderAgent(currentAgent);
});

// ── End chat: email the transcript + thank-you note ────────────
function sentTranscriptKey(agent) { return `iraniyo_transcript_sent_${agent}`; }
function markTranscriptSent(agent) { localStorage.setItem(sentTranscriptKey(agent), 'true'); }
function transcriptAlreadySent(agent) { return localStorage.getItem(sentTranscriptKey(agent)) === 'true'; }

function buildTranscriptPayload(agent) {
    return JSON.stringify({
        to_email: userInfo.email,
        to_name: userInfo.name || '',
        agent_slug: agent,
        messages: loadStoredMessages(agent).map(m => ({ role: m.role, text: m.text || '' })),
    });
}

endChatBtn.addEventListener('click', async () => {
    if (thinking || endChatBtn.disabled) return;

    if (!userInfo?.email) {
        await showModal({
            icon: 'fa-circle-exclamation',
            title: 'ایمیل ثبت نشده است',
            message: 'برای ارسال خلاصه گفتگو، ابتدا ایمیل خود را در ابتدای گفتگو وارد کنید.',
        });
        return;
    }
    const messages = loadStoredMessages(currentAgent);
    if (!messages.length) {
        await showModal({
            icon: 'fa-circle-exclamation',
            title: 'گفتگویی یافت نشد',
            message: 'هنوز گفتگویی برای ارسال وجود ندارد.',
        });
        return;
    }
    const ok = await showModal({
        icon: 'fa-envelope-circle-check',
        title: 'پایان گفتگو',
        message: 'گفتگو به پایان برسد و خلاصه آن به ایمیل شما ارسال شود؟',
        okLabel: 'بله، ارسال شود',
        cancelLabel: 'انصراف',
    });
    if (!ok) return;

    const originalIcon = endChatBtn.innerHTML;
    endChatBtn.disabled = true;
    endChatBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';

    try {
        const res = await fetch(TRANSCRIPT_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: buildTranscriptPayload(currentAgent),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || err.error || 'ارسال ایمیل ناموفق بود');
        }
        markTranscriptSent(currentAgent);
        endChatBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
        await showModal({
            icon: 'fa-circle-check',
            title: 'ارسال شد',
            message: 'خلاصه گفتگو به ایمیل شما ارسال شد. سپاسگزاریم! 💜',
        });
    } catch (err) {
        console.error('Send transcript error:', err);
        endChatBtn.innerHTML = '<i class="fa-solid fa-xmark"></i>';
        await showModal({
            icon: 'fa-circle-exclamation',
            title: 'خطا در ارسال',
            message: 'متأسفانه ارسال ایمیل با خطا مواجه شد. لطفاً بعداً دوباره تلاش کنید.',
        });
    } finally {
        setTimeout(() => {
            endChatBtn.innerHTML = originalIcon;
            endChatBtn.disabled = false;
        }, 2000);
    }
});

// Automatically email the transcript once when the user leaves the page,
// so they still get a summary even if they forget to tap "End chat".
window.addEventListener('pagehide', () => {
    if (!userInfo?.email) return;
    if (transcriptAlreadySent(currentAgent)) return;
    if (!loadStoredMessages(currentAgent).length) return;
    const blob = new Blob([buildTranscriptPayload(currentAgent)], { type: 'application/json' });
    if (navigator.sendBeacon(TRANSCRIPT_URL, blob)) {
        markTranscriptSent(currentAgent);
    }
});

// ── Voice chat: text-to-speech (bot replies) ──────────────────
const VOICE_KEY = 'iraniyo_voice_enabled';
let voiceEnabled = localStorage.getItem(VOICE_KEY) === 'true';
let persianVoice = null;

function pickPersianVoice() {
    if (!window.speechSynthesis) return;
    const voices = speechSynthesis.getVoices();
    persianVoice = voices.find(v => /^fa/i.test(v.lang)) || null;
}

function cancelSpeech() {
    if (window.speechSynthesis) speechSynthesis.cancel();
}

function speak(text) {
    if (!voiceEnabled || !window.speechSynthesis || !text) return;
    cancelSpeech();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = persianVoice?.lang || 'fa-IR';
    if (persianVoice) utterance.voice = persianVoice;
    speechSynthesis.speak(utterance);
}

if (window.speechSynthesis) {
    pickPersianVoice();
    speechSynthesis.addEventListener('voiceschanged', pickPersianVoice);
    voiceToggleBtn.classList.toggle('active', voiceEnabled);
    voiceToggleBtn.addEventListener('click', () => {
        voiceEnabled = !voiceEnabled;
        localStorage.setItem(VOICE_KEY, voiceEnabled ? 'true' : 'false');
        voiceToggleBtn.classList.toggle('active', voiceEnabled);
        if (!voiceEnabled) cancelSpeech();
    });
} else {
    voiceToggleBtn.style.display = 'none';
}

// ── Voice chat: speech-to-text (mic input) ────────────────────
const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;

if (SpeechRecognitionAPI) {
    recognition = new SpeechRecognitionAPI();
    recognition.lang = 'fa-IR';
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.addEventListener('result', (e) => {
        const transcript = e.results[0][0].transcript.trim();
        if (transcript) {
            chatInput.value = transcript;
            chatInput.style.height = 'auto';
            chatInput.style.height = chatInput.scrollHeight + 'px';
            sendBtn.disabled = false;
            chatForm.requestSubmit();
        }
    });
    recognition.addEventListener('end', () => micBtn.classList.remove('recording'));
    recognition.addEventListener('error', () => micBtn.classList.remove('recording'));

    micBtn.addEventListener('click', () => {
        if (thinking) return;
        if (micBtn.classList.contains('recording')) {
            recognition.stop();
            return;
        }
        cancelSpeech();
        try {
            recognition.start();
            micBtn.classList.add('recording');
        } catch (err) {
            console.error('Speech recognition error:', err);
        }
    });
} else {
    micBtn.classList.add('unsupported');
}

// ── Init ──────────────────────────────────────────────────────
(async function init() {
    await loadAgentsMeta();
    agentTabs.querySelectorAll('.agent-tab').forEach(b => b.classList.toggle('active', b.dataset.agent === currentAgent));
    if (chatInterface.style.display !== 'none') renderAgent(currentAgent);
})();

// ── Send message ─────────────────────────────────────────────
chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const messageText = chatInput.value.trim();
    if (!messageText || thinking) return;

    // Check paywall via Server (IP tracking)
    let currentCount = 0;
    try {
        const countResp = await fetch('counter.php?action=increment');
        const countData = await countResp.json();
        currentCount = countData.count;
    } catch (err) {
        questionCount++;
        localStorage.setItem('n8n_chat_qcount', questionCount);
        currentCount = questionCount;
    }

    if (currentCount >= AD_THRESHOLD) {
        pendingMessage = messageText;
        showAdOverlay();
        return;
    }

    const agent = currentAgent;
    const meta = AGENTS_META[agent];

    // Build client-side history (Gemini format) BEFORE persisting this new
    // message — agents like the trip planner manage their own state.
    const clientHistory = meta?.use_client_history ? buildClientHistory(agent) : undefined;

    // Reset input
    chatInput.value = '';
    chatInput.style.height = 'auto';
    sendBtn.disabled = true;
    thinking = true;

    // Add user message to UI
    addMessage(messageText, 'user');

    // Show typing indicator
    const typingIndicator = addTypingIndicator();
    if (window.setNeuralSpeed) setNeuralSpeed(true);

    try {
        const payload = {
            message: messageText,
            agent_slug: agent,
        };
        const sid = getSessionId(agent);
        if (sid) payload.session_id = sid;
        if (clientHistory) payload.client_history = clientHistory;
        if (userInfo?.name) payload.user_name = userInfo.name;
        if (userInfo?.email) payload.user_email = userInfo.email;

        const response = await fetch(CHAT_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        // Remove typing indicator
        typingIndicator.remove();
        if (window.setNeuralSpeed) setNeuralSpeed(false);

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || data.detail || `HTTP error! status: ${response.status}`);
        }

        if (data.session_id) setSessionId(agent, data.session_id);

        const textResponse = (data.answer && data.answer.trim()) || 'هیچ پاسخی دریافت نشد.';
        addMessage(textResponse, 'bot');

        // Play notification sound
        notificationSound.play().catch(() => {});
    } catch (error) {
        typingIndicator.remove();
        if (window.setNeuralSpeed) setNeuralSpeed(false);
        console.error('Error contacting chat backend:', error);
        addMessage('متاسفانه در ارتباط با سرور مشکلی پیش آمد. لطفا مجددا تلاش کنید.', 'bot', false);
    } finally {
        thinking = false;
        sendBtn.disabled = chatInput.value.trim() === '';
        chatInput.focus();
    }
});

// --- Ad Paywall Logic ---
let ytPlayer;
let ytVideoEnded = false;

// Helper to extract Video ID from URL
function getYouTubeID(url) {
    const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
    const match = url.match(regExp);
    return (match && match[2].length === 11) ? match[2] : url; // fallback to url if it's already an ID
}

let currentVideoIndex = parseInt(localStorage.getItem('n8n_chat_vid_idx') || '0');

function getNextVideoId() {
    if (!YOUTUBE_VIDEOS.length) return null;
    const link = YOUTUBE_VIDEOS[currentVideoIndex];
    const id = getYouTubeID(link);

    // Increment index for next time
    currentVideoIndex = (currentVideoIndex + 1) % YOUTUBE_VIDEOS.length;
    localStorage.setItem('n8n_chat_vid_idx', currentVideoIndex);

    return id;
}

// Called automatically by the YouTube Iframe API
function onYouTubeIframeAPIReady() {}

function initYouTubePlayer() {
    if (ytPlayer) return;
    const videoId = getNextVideoId();
    if (!videoId) return;

    ytPlayer = new YT.Player('ytplayer', {
        height: '100%',
        width: '100%',
        videoId: videoId,
        playerVars: {
            'autoplay': 1,
            'mute': 0,
            'playsinline': 1,
            'controls': 0,
            'rel': 0,
            'enablejsapi': 1,
            'modestbranding': 1,
            'fs': 0,
            'origin': window.location.origin
        },
        events: {
            'onReady': (event) => {
                event.target.playVideo();
            },
            'onStateChange': onPlayerStateChange,
            'onError': onPlayerError
        }
    });
}

function onPlayerReady(event) {}

function onPlayerError(event) {
    console.error("YouTube Player Error:", event.data);
    // Fallback: if video fails, let them skip
    ytVideoEnded = true;
    skipAdBtn.textContent = 'ادامه گفتگو (خطا در ویدیو)';
    skipAdBtn.disabled = false;
    skipAdBtn.classList.add('ready');
}

function onPlayerStateChange(event) {
    if (event.data === YT.PlayerState.PLAYING) {
        document.getElementById('ytFacade').style.display = 'none';
    }
    // When video ends
    if (event.data === YT.PlayerState.ENDED) {
        ytVideoEnded = true;
        skipAdBtn.textContent = 'ادامه گفتگو';
        skipAdBtn.disabled = false;
        skipAdBtn.classList.add('ready');
    }
}

let resetToken = '';

function showAdOverlay() {
    adOverlay.style.display = 'flex';
    document.getElementById('ytFacade').style.display = 'flex';
    skipAdBtn.disabled = true;
    skipAdBtn.classList.remove('ready');
    skipAdBtn.textContent = 'لطفاً ویدیو را تا انتها تماشا کنید...';
    ytVideoEnded = false;
    resetToken = '';
    // C2 fix: fetch a server-signed token required to reset the counter
    fetch('counter.php?action=get_token')
        .then(r => r.json())
        .then(d => { resetToken = d.token || ''; })
        .catch(() => {});

    // Initialize or Play the YouTube video
    if (!ytPlayer) {
        initYouTubePlayer();
    } else {
        const nextVideoId = getNextVideoId();

        if (ytPlayer.loadVideoById) {
            ytPlayer.loadVideoById(nextVideoId);
        } else {
            // If player methods aren't ready, try to play what we have
            ytPlayer.playVideo();
        }
    }
}

document.getElementById('customPlayBtn').addEventListener('click', () => {
    if (ytPlayer) {
        ytPlayer.playVideo();
    }
});

skipAdBtn.addEventListener('click', async () => {
    if (!ytVideoEnded) return; // Prevent clicking if not ended

    adOverlay.style.display = 'none';

    // Pause video when closed
    if (ytPlayer && ytPlayer.pauseVideo) {
        ytPlayer.pauseVideo();
    }

    // C2 fix: reset requires server-signed token
    try {
        await fetch('counter.php?action=reset&token=' + encodeURIComponent(resetToken));
    } catch (e) { }

    questionCount = 0; // Reset local fallback counter
    localStorage.setItem('n8n_chat_qcount', 0);

    if (pendingMessage) {
        // Auto-submit the pending message
        chatInput.value = pendingMessage;
        chatForm.requestSubmit();
        pendingMessage = '';
    }
});

// ── Neural Network Background ──────────────────────────
(function () {
    const canvas = document.getElementById('neuralCanvas');
    const ctx = canvas.getContext('2d');
    const NODE_COUNT = 80;
    const MAX_DIST = 200;
    const BASE_SPEED = 0.4;
    let speedMultiplier = 1;
    const nodes = [];

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }

    function rand(a, b) { return a + Math.random() * (b - a); }

    for (let i = 0; i < NODE_COUNT; i++) {
        const speed = rand(BASE_SPEED * 0.5, BASE_SPEED);
        const angle = rand(0, Math.PI * 2);
        nodes.push({
            x: rand(0, window.innerWidth),
            y: rand(0, window.innerHeight),
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed,
            r: rand(1.5, 3)
        });
    }

    function draw() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        for (let i = 0; i < nodes.length; i++) {
            const a = nodes[i];
            for (let j = i + 1; j < nodes.length; j++) {
                const b = nodes[j];
                const dx = a.x - b.x;
                const dy = a.y - b.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < MAX_DIST) {
                    const alpha = (1 - dist / MAX_DIST) * 0.9;
                    ctx.beginPath();
                    ctx.strokeStyle = `rgba(139, 92, 246, ${alpha})`;
                    ctx.lineWidth = 0.7;
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.stroke();
                }
            }
        }

        for (const n of nodes) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(167, 139, 250, 0.95)';
            ctx.fill();

            n.x += n.vx * speedMultiplier;
            n.y += n.vy * speedMultiplier;
            if (n.x < 0 || n.x > canvas.width)  n.vx *= -1;
            if (n.y < 0 || n.y > canvas.height) n.vy *= -1;
        }

        requestAnimationFrame(draw);
    }

    window.setNeuralSpeed = function (fast) {
        const target = fast ? 4 : 1;
        const step = fast ? 0.15 : 0.08;
        const interval = setInterval(() => {
            speedMultiplier += (target - speedMultiplier) * step;
            if (Math.abs(speedMultiplier - target) < 0.01) {
                speedMultiplier = target;
                clearInterval(interval);
            }
        }, 16);
    };

    resize();
    window.addEventListener('resize', resize);
    draw();
})();
