<?php
// Shared CORS helper — included by proxy.php, counter.php, save_user.php
function checkCors($methods = 'POST') {
    $origin = isset($_SERVER['HTTP_ORIGIN']) ? $_SERVER['HTTP_ORIGIN'] : '';
    $host   = $_SERVER['SERVER_NAME'] ?? 'localhost';
    $isLocal = in_array($host, ['localhost', '127.0.0.1', '::1'])
               || preg_match('/^(localhost|127\.0\.0\.1)(:\d+)?$/', $host);

    if ($isLocal) {
        // Development: allow any localhost origin regardless of port/protocol
        $allowed = empty($origin) || (bool) preg_match('#^https?://(localhost|127\.0\.0\.1)(:\d+)?$#', $origin);
        $headerOrigin = $origin ?: '*';
    } else {
        // Production: exact HTTPS match only
        $headerOrigin = 'https://' . $host;
        $allowed = empty($origin) || $origin === $headerOrigin;
    }

    if (!$allowed) {
        http_response_code(403);
        echo json_encode(['error' => 'Forbidden']);
        exit;
    }

    header('Access-Control-Allow-Origin: ' . $headerOrigin);
    header('Access-Control-Allow-Methods: ' . $methods . ', OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type');
    header('Vary: Origin');

    if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
        http_response_code(204);
        exit;
    }
}
