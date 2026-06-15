<?php
// ── Proxy: exposes the Python backend's agent list (icons, names, welcome
// messages, chips) so the widget can render multiple agents from one source
// of truth (admin-editable in dryas_chatbot). ──
header('Content-Type: application/json');

require_once __DIR__ . '/cors.php';
checkCors('GET');

// Override with DRYAS_BACKEND_URL env var if the FastAPI app runs elsewhere.
define('DRYAS_BACKEND', rtrim(getenv('DRYAS_BACKEND_URL') ?: 'http://127.0.0.1:8000', '/'));

$ch = curl_init(DRYAS_BACKEND . '/agents');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT        => 10,
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
