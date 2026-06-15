<?php
// ── Proxy: forwards "end chat" transcript emails to the local Python (FastAPI / dryas_chatbot) backend ──
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

if (!$data || empty($data['to_email'])) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid request']);
    exit;
}

$toEmail = trim((string) $data['to_email']);
if (!filter_var($toEmail, FILTER_VALIDATE_EMAIL)) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid email address']);
    exit;
}

$toName = isset($data['to_name']) ? mb_substr(strip_tags((string) $data['to_name']), 0, 120) : '';

// Only agents exposed by this widget
$allowedAgents = get_allowed_agents();
$agentSlug = (isset($data['agent_slug']) && in_array($data['agent_slug'], $allowedAgents, true))
    ? $data['agent_slug']
    : 'dr-yas';

$messages = [];
if (isset($data['messages']) && is_array($data['messages'])) {
    foreach (array_slice($data['messages'], -100) as $m) {
        if (!is_array($m) || !isset($m['role'])) continue;
        $role = $m['role'] === 'bot' ? 'bot' : 'user';
        $messages[] = [
            'role' => $role,
            'text' => mb_substr(strip_tags((string) ($m['text'] ?? '')), 0, 4000),
        ];
    }
}

$payload = [
    'to_email'   => $toEmail,
    'to_name'    => $toName,
    'agent_slug' => $agentSlug,
    'messages'   => $messages,
];

// Forward to the Python backend
$ch = curl_init(DRYAS_BACKEND . '/send-transcript');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST           => true,
    CURLOPT_POSTFIELDS     => json_encode($payload),
    CURLOPT_HTTPHEADER     => ['Content-Type: application/json', 'Accept: application/json'],
    CURLOPT_TIMEOUT        => 60,
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

http_response_code($httpCode ?: 200);
echo $response;
