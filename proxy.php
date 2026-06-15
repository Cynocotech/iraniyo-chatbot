<?php
// ── Proxy: forwards chat requests to the local Python (FastAPI / dryas_chatbot) backend ──
header('Content-Type: application/json');

require_once __DIR__ . '/cors.php';
require_once __DIR__ . '/agent_helper.php';
checkCors('POST');

// Override with DRYAS_BACKEND_URL env var if the FastAPI app runs elsewhere.
define('DRYAS_BACKEND', rtrim(getenv('DRYAS_BACKEND_URL') ?: 'http://127.0.0.1:8000', '/'));

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

$raw  = file_get_contents('php://input');
$data = json_decode($raw, true);

if (!$data || !isset($data['message'])) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid request']);
    exit;
}

// Sanitize message
$message = strip_tags((string) $data['message']);
$message = trim(mb_substr($message, 0, 2000));
if ($message === '') {
    http_response_code(400);
    echo json_encode(['error' => 'Empty message']);
    exit;
}

// Sanitize session_id (alphanumeric, underscore, hyphen only)
$sessionId = null;
if (!empty($data['session_id'])) {
    $sessionId = preg_replace('/[^A-Za-z0-9_\-]/', '', (string) $data['session_id']);
    $sessionId = mb_substr($sessionId, 0, 64);
    if ($sessionId === '') $sessionId = null;
}

// Only agents exposed by this widget
$allowedAgents = get_allowed_agents();
$agentSlug = (isset($data['agent_slug']) && in_array($data['agent_slug'], $allowedAgents, true))
    ? $data['agent_slug']
    : 'dr-yas';

$payload = [
    'message'    => $message,
    'agent_slug' => $agentSlug,
];
if ($sessionId !== null) {
    $payload['session_id'] = $sessionId;
}

if (isset($data['user_name'])) {
    $userName = trim(mb_substr(strip_tags((string) $data['user_name']), 0, 120));
    if ($userName !== '') {
        $payload['user_name'] = $userName;
    }
}
if (isset($data['user_email'])) {
    $userEmail = filter_var((string) $data['user_email'], FILTER_VALIDATE_EMAIL);
    if ($userEmail) {
        $payload['user_email'] = $userEmail;
    }
}

// client_history — only used by agents with use_client_history (e.g. trip-planner)
if (isset($data['client_history']) && is_array($data['client_history'])) {
    $history = [];
    foreach (array_slice($data['client_history'], -40) as $turn) {
        if (!is_array($turn) || !isset($turn['role'], $turn['content'])) continue;
        $role = $turn['role'] === 'assistant' ? 'assistant' : 'user';
        $history[] = [
            'role'    => $role,
            'content' => mb_substr(strip_tags((string) $turn['content']), 0, 4000),
        ];
    }
    $payload['client_history'] = $history;
}

// Forward to the Python backend
$ch = curl_init(DRYAS_BACKEND . '/chat');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST           => true,
    CURLOPT_POSTFIELDS     => json_encode($payload),
    CURLOPT_HTTPHEADER     => ['Content-Type: application/json', 'Accept: application/json'],
    CURLOPT_TIMEOUT        => 150,
    CURLOPT_SSL_VERIFYPEER => true,
]);

$response = curl_exec($ch);
$httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curlErr  = curl_error($ch);

if ($curlErr) {
    http_response_code(502);
    echo json_encode(['error' => 'Upstream error', 'detail' => $curlErr]);
    exit;
}

http_response_code($httpCode);
echo $response;
