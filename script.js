// YouTube links loaded from videos.json at runtime
let YOUTUBE_VIDEOS = [];
fetch('videos.json')
    .then(r => r.json())
    .then(d => { YOUTUBE_VIDEOS = d.videos || []; })
    .catch(() => {});

const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const quickBtns = document.querySelectorAll('.quick-btn');

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

// C1 fix: all AI requests go through server-side proxy (real webhook URL never exposed)
const WEBHOOK_URL = 'proxy.php';

const notificationSound = new Audio('https://assets.mixkit.co/active_storage/sfx/2354/2354-preview.mp3');

// Set up sessionId
let sessionId = localStorage.getItem('n8n_chat_session');
if (!sessionId) {
    sessionId = 'chat_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('n8n_chat_session', sessionId);
}

// User info handling
let userInfo = JSON.parse(localStorage.getItem('n8n_chat_user'));

const chatInputContainer = document.getElementById('chatInputContainer');
const quickQuestionsWrapper = document.getElementById('quickQuestionsWrapper');

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
    sendInitGreeting();
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
            body: JSON.stringify({ name, email })
        }).catch(() => {});
        showChat();
        sendInitGreeting();
    }
});

async function sendInitGreeting() {
    const typingIndicator = addTypingIndicator();

    try {
        const response = await fetch(WEBHOOK_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                action: "sendMessage",
                sessionId: sessionId,
                chatInput: `سلام. نام من ${userInfo.name} است. لطفا به من خوش‌آمد بگو و بپرس چطور می‌توانی کمکم کنی.`,
                metadata: userInfo
            })
        });

        typingIndicator.remove();

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        let textResponse = '';
        const contentType = response.headers.get("content-type");

        if (contentType && contentType.indexOf("application/json") !== -1) {
            const data = await response.json();
            if (Array.isArray(data) && data.length > 0) {
                textResponse = data[0].output || data[0].text || data[0].chatResponse || JSON.stringify(data[0]);
            } else if (data.output || data.text || data.chatResponse) {
                textResponse = data.output || data.text || data.chatResponse;
            } else {
                textResponse = JSON.stringify(data);
            }
        } else {
            textResponse = await response.text();
        }

        if (!textResponse || textResponse.trim() === '') {
            textResponse = `سلام ${userInfo.name}! من دستیار هوشمند ایرانیو هستم. چطور می‌توانم کمکتان کنم؟`;
        }

        appendMessage(textResponse, 'bot');
        notificationSound.play().catch(() => {});
    } catch (error) {
        typingIndicator.remove();
        console.error('Init greeting error:', error);
        appendMessage(`سلام ${userInfo.name}! متاسفانه در ارتباط با سرور مشکلی پیش آمد.`, 'bot');
    }
}

// Auto-resize textarea
chatInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
    if (this.value.trim() === '') {
        sendBtn.disabled = true;
    } else {
        sendBtn.disabled = false;
    }
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

// Quick questions handler
quickBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        chatInput.value = btn.textContent;
        chatInput.style.height = 'auto'; // Reset height
        sendBtn.disabled = false;
        chatForm.requestSubmit();
    });
});

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const messageText = chatInput.value.trim();
    if (!messageText) return;

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

    if (currentCount >= 5) {
        pendingMessage = messageText;
        showAdOverlay();
        return;
    }

    // Reset input
    chatInput.value = '';
    chatInput.style.height = 'auto';
    sendBtn.disabled = true;

    // Add user message to UI
    appendMessage(messageText, 'user');

    // Show typing indicator
    const typingIndicator = addTypingIndicator();
    if (window.setNeuralSpeed) setNeuralSpeed(true);
    scrollToBottom();

    try {
        const response = await fetch(WEBHOOK_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify({
                action: "sendMessage",
                sessionId: sessionId,
                chatInput: messageText,
                metadata: userInfo // Send name and email to n8n
            })
        });

        // Remove typing indicator
        typingIndicator.remove();
        if (window.setNeuralSpeed) setNeuralSpeed(false);

        if (!response.ok) {
            if (response.status === 404) {
                throw new Error("Webhook 404: The n8n workflow is either inactive or not listening.");
            }
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        // Parse response
        let textResponse = '';
        const contentType = response.headers.get("content-type");

        if (contentType && contentType.indexOf("application/json") !== -1) {
            const data = await response.json();

            // Handle n8n array response structure
            if (Array.isArray(data) && data.length > 0) {
                textResponse = data[0].output || data[0].text || data[0].chatResponse || JSON.stringify(data[0]);
            }
            // Handle n8n object response structure
            else if (data.output || data.text || data.chatResponse) {
                textResponse = data.output || data.text || data.chatResponse;
            } else {
                textResponse = JSON.stringify(data);
            }
        } else {
            // Handle plain text response
            textResponse = await response.text();
        }

        if (!textResponse || textResponse.trim() === '') {
            textResponse = 'هیچ پاسخی دریافت نشد.';
        }

        appendMessage(textResponse, 'bot');

        // Play notification sound
        notificationSound.play().catch(() => {});
    } catch (error) {
        typingIndicator.remove();
        if (window.setNeuralSpeed) setNeuralSpeed(false);
        console.error('Error contacting n8n webhook:', error);

        let errorMsg = 'متاسفانه در ارتباط با سرور مشکلی پیش آمد. لطفا مجددا تلاش کنید.';
        if (error.message.includes('Webhook 404')) {
            errorMsg = 'سرور n8n در دسترس نیست (خطای ۴۰۴). لطفاً مطمئن شوید که ورک‌فلو در n8n **فعال (Active)** است یا دکمه **Execute Workflow** را برای تست زده‌اید.';
        }

        appendMessage(errorMsg, 'bot');
    }
});

function appendMessage(text, sender) {
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message', sender);

    const avatarDiv = document.createElement('div');
    avatarDiv.classList.add('msg-avatar');
    if (sender === 'bot') {
        avatarDiv.innerHTML = '<i class="fa-solid fa-robot"></i>';
    } else {
        avatarDiv.innerHTML = '<i class="fa-solid fa-user"></i>';
    }

    const contentDiv = document.createElement('div');
    contentDiv.classList.add('message-content');

    if (sender === 'bot') {
        const raw = typeof marked !== 'undefined' ? marked.parse(text) : text.replace(/\n/g, '<br>');
        // C4 fix: sanitize rendered HTML to prevent XSS
        contentDiv.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(raw) : raw;
    } else {
        // User text is plain text, escape it properly
        contentDiv.textContent = text;
    }

    msgDiv.appendChild(avatarDiv);
    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    scrollToBottom();
}

function addTypingIndicator() {
    const indicator = document.createElement('div');
    indicator.classList.add('typing-indicator', 'active', 'message', 'bot');
    indicator.innerHTML = `
        <div class="msg-avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-content" style="display: flex; align-items: center; gap: 8px;">
            <i class="fa-solid fa-circle-notch fa-spin" style="color: var(--primary-hover); font-size: 1.1rem;"></i>
            <span style="font-size: 0.9rem; color: var(--text-main);">در حال بررسی و جستجو...</span>
        </div>
    `;
    chatMessages.appendChild(indicator);
    return indicator;
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Initial state
sendBtn.disabled = true;
chatInput.focus();

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
