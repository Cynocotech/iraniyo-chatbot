<?php
header('Content-Type: application/json');

require_once __DIR__ . '/cors.php';
checkCors('GET');

// C2 fix: secret used to sign reset tokens (change this to a random string)
define('RESET_SECRET', 'a509ad6520aa5df5a49dd421a72fbecd0892dfd1e91efbd7430b38df58f985e6');

// H3 fix: real IP detection through Cloudflare / proxies
function getClientIP() {
    if (!empty($_SERVER['HTTP_CF_CONNECTING_IP'])) {
        $ip = filter_var($_SERVER['HTTP_CF_CONNECTING_IP'], FILTER_VALIDATE_IP);
        if ($ip) return $ip;
    }
    if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) {
        foreach (explode(',', $_SERVER['HTTP_X_FORWARDED_FOR']) as $part) {
            $ip = filter_var(trim($part), FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE);
            if ($ip) return $ip;
        }
    }
    return $_SERVER['REMOTE_ADDR'];
}

// C2 fix: HMAC token valid for the current 5-minute window
function generateToken($ip) {
    $window = floor(time() / 300);
    return hash_hmac('sha256', $ip . '|' . $window, RESET_SECRET);
}

function verifyToken($ip, $token) {
    $window = floor(time() / 300);
    // Accept current and previous window (up to ~10 min tolerance)
    return hash_equals(generateToken($ip), $token)
        || hash_equals(hash_hmac('sha256', $ip . '|' . ($window - 1), RESET_SECRET), $token);
}

$file   = __DIR__ . '/ip_counts.json';
$ip     = getClientIP();
$action = isset($_GET['action']) ? $_GET['action'] : 'increment';

// C3 fix: file locking prevents race conditions
$fp = fopen($file, 'c+');
if (!$fp) {
    http_response_code(500);
    echo json_encode(['error' => 'Server error']);
    exit;
}
flock($fp, LOCK_EX);

$content = stream_get_contents($fp);
$data    = $content ? json_decode($content, true) : [];
if (!is_array($data)) $data = [];

if ($action === 'get_token') {
    // Issue a signed reset token for this IP
    flock($fp, LOCK_UN);
    fclose($fp);
    echo json_encode(['token' => generateToken($ip)]);
    exit;

} elseif ($action === 'reset') {
    // C2 fix: require valid token before resetting
    $token = isset($_GET['token']) ? $_GET['token'] : '';
    if (!$token || !verifyToken($ip, $token)) {
        flock($fp, LOCK_UN);
        fclose($fp);
        http_response_code(403);
        echo json_encode(['error' => 'Invalid token']);
        exit;
    }
    $data[$ip] = 0;

} elseif ($action === 'check') {
    // Read only, no write needed
    flock($fp, LOCK_UN);
    fclose($fp);
    echo json_encode(['count' => isset($data[$ip]) ? (int)$data[$ip] : 0]);
    exit;

} else {
    // Default: increment
    $data[$ip] = isset($data[$ip]) ? (int)$data[$ip] + 1 : 1;
}

// Write back with lock still held
ftruncate($fp, 0);
rewind($fp);
fwrite($fp, json_encode($data));
flock($fp, LOCK_UN);
fclose($fp);

// M2 fix: do NOT return the IP in the response
echo json_encode(['count' => (int)$data[$ip]]);
