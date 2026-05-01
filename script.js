// ⬇️ CONFIGURATION: Add your unlimited YouTube links or IDs here ⬇️
const YOUTUBE_VIDEOS = [
    "https://youtu.be/bj1JRuyYeco?si=WIyH_J-NHum07p1E",
    "https://youtu.be/BI0cTPdsGAE?si=2bWRVIzLiBF2wdlu",
    "https://youtu.be/2mRycClopmA?si=5p3_7BKyvOD-xWuJ",


];

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

// API Webhook configuration
// For testing locally in n8n (requires you to click 'Execute Workflow'):
const WEBHOOK_URL = 'https://agent.iraniyo.uk/webhook/a19aac9b-473e-4ad1-a220-4edd4f1025f4/chat';

// Notification Sound
const notificationSound = new Audio('https://assets.mixkit.co/active_storage/sfx/2354/2354-preview.mp3');

// For production on your server (requires workflow to be Active):
// const WEBHOOK_URL = 'https://agent.iraniyo.uk/webhook/a19aac9b-473e-4ad1-a220-4edd4f1025f4/chat';

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
        notificationSound.play().catch(e => console.log('Audio play failed:', e));
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
        console.log("Current Question Count (Server):", currentCount);
    } catch (err) {
        // Fallback to local storage if PHP is not available
        questionCount++;
        localStorage.setItem('n8n_chat_qcount', questionCount);
        currentCount = questionCount;
        console.log("Current Question Count (Local Fallback):", currentCount);
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
        notificationSound.play().catch(e => console.log('Audio play failed:', e));
    } catch (error) {
        typingIndicator.remove();
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
        // N8n might send HTML with <b> and <a> tags as configured in the prompt
        // Using marked parser but allowing basic HTML tags through
        if (typeof marked !== 'undefined') {
            contentDiv.innerHTML = marked.parse(text);
        } else {
            contentDiv.innerHTML = text.replace(/\\n/g, '<br>');
        }
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
    const link = YOUTUBE_VIDEOS[currentVideoIndex];
    const id = getYouTubeID(link);
    
    // Increment index for next time
    currentVideoIndex = (currentVideoIndex + 1) % YOUTUBE_VIDEOS.length;
    localStorage.setItem('n8n_chat_vid_idx', currentVideoIndex);
    
    console.log("Selected Sequential Video ID:", id);
    return id;
}

// This function is called automatically by the YouTube Iframe API
function onYouTubeIframeAPIReady() {
    console.log("YouTube API Loaded. Player will initialize when needed.");
}

function initYouTubePlayer() {
    if (ytPlayer) return; // Already initialized
    
    const videoId = getNextVideoId();
    console.log("Initializing YouTube Player with ID:", videoId);

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

function onPlayerReady(event) {
    console.log("YouTube Player Ready");
}

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

function showAdOverlay() {
    adOverlay.style.display = 'flex';
    document.getElementById('ytFacade').style.display = 'flex';
    skipAdBtn.disabled = true;
    skipAdBtn.classList.remove('ready');
    skipAdBtn.textContent = 'لطفاً ویدیو را تا انتها تماشا کنید...';
    ytVideoEnded = false;

    // Load Google Ad
    try {
        (adsbygoogle = window.adsbygoogle || []).push({});
    } catch (e) { console.error("AdSense Error: ", e); }

    // Initialize or Play the YouTube video
    if (!ytPlayer) {
        initYouTubePlayer();
    } else {
        const nextVideoId = getNextVideoId();
        console.log("Switching to next sequential video:", nextVideoId);
        
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

    // Reset counter on Server
    try {
        await fetch('counter.php?action=reset');
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
