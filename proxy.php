<?php
// ── Proxy: hides the n8n webhook URL from client-side code ──
header('Content-Type: application/json');

require_once __DIR__ . '/cors.php';
checkCors('POST');

define('N8N_WEBHOOK', 'https://agent.iraniyo.uk/webhook/a19aac9b-473e-4ad1-a220-4edd4f1025f4/chat');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

$raw  = file_get_contents('php://input');
$data = json_decode($raw, true);

if (!$data || !isset($data['action'])) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid request']);
    exit;
}

// M3 fix: sanitize user-supplied name and email before forwarding
if (isset($data['metadata']['name'])) {
    $data['metadata']['name'] = mb_substr(
        preg_replace('/[<>{}"\'\\\\]/u', '', $data['metadata']['name']),
        0, 100
    );
}
if (isset($data['metadata']['email'])) {
    $data['metadata']['email'] = filter_var(
        $data['metadata']['email'], FILTER_SANITIZE_EMAIL
    );
}
// Sanitize chatInput to strip any HTML/script injection in the prompt
if (isset($data['chatInput'])) {
    $data['chatInput'] = strip_tags($data['chatInput']);
    $data['chatInput'] = mb_substr($data['chatInput'], 0, 2000);
}

// Forward to n8n via server-side curl (webhook URL stays hidden)
$ch = curl_init(N8N_WEBHOOK);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST           => true,
    CURLOPT_POSTFIELDS     => json_encode($data),
    CURLOPT_HTTPHEADER     => ['Content-Type: application/json', 'Accept: application/json'],
    CURLOPT_TIMEOUT        => 30,
    CURLOPT_SSL_VERIFYPEER => true,
]);

$response = curl_exec($ch);
$httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curlErr  = curl_error($ch);
curl_close($ch);

if ($curlErr) {
    http_response_code(502);
    echo json_encode(['error' => 'Upstream error']);
    exit;
}

http_response_code($httpCode);
echo $response;
