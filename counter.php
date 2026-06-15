<?php
header('Content-Type: application/json');

require_once __DIR__ . '/cors.php';
checkCors('GET');

// C2 fix: secret used to sign reset tokens. Set IRANIYO_COUNTER_RESET_SECRET to
// pin it across deploys; otherwise a random secret is generated once and
// stored alongside this file (not committed to git).
function loadResetSecret(): string {
    $envSecret = getenv('IRANIYO_COUNTER_RESET_SECRET');
    if ($envSecret) {
        return $envSecret;
    }
    $secretFile = __DIR__ . '/.counter_secret';
    if (!is_file($secretFile)) {
        file_put_contents($secretFile, bin2hex(random_bytes(32)), LOCK_EX);
        chmod($secretFile, 0600);
    }
    return trim(file_get_contents($secretFile));
}
define('RESET_SECRET', loadResetSecret());

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

$ip     = getClientIP();
$action = isset($_GET['action']) ? $_GET['action'] : 'increment';

if ($action === 'get_token') {
    // Issue a signed reset token for this IP
    echo json_encode(['token' => generateToken($ip)]);
    exit;
}

$dbUrl = getenv('POSTGRES_DSN');
if (!$dbUrl) {
    http_response_code(500);
    echo json_encode(['error' => 'POSTGRES_DSN not configured']);
    exit;
}

$dbopts = parse_url($dbUrl);
if (!$dbopts) {
    http_response_code(500);
    echo json_encode(['error' => 'Invalid POSTGRES_DSN']);
    exit;
}

$dsn = 'pgsql:host=' . ($dbopts['host'] ?? '127.0.0.1') . ';port=' . ($dbopts['port'] ?? 5432) . ';dbname=' . ltrim($dbopts['path'] ?? '', '/');
try {
    $pdo = new PDO($dsn, $dbopts['user'] ?? null, $dbopts['pass'] ?? null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC
    ]);

    $pdo->exec("CREATE TABLE IF NOT EXISTS ip_counts (
        ip VARCHAR(45) PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    )");
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Database connection failed']);
    exit;
}

$count = 0;

if ($action === 'reset') {
    // C2 fix: require valid token before resetting
    $token = isset($_GET['token']) ? $_GET['token'] : '';
    if (!$token || !verifyToken($ip, $token)) {
        http_response_code(403);
        echo json_encode(['error' => 'Invalid token']);
        exit;
    }
    $stmt = $pdo->prepare("UPDATE ip_counts SET count = 0 WHERE ip = ?");
    $stmt->execute([$ip]);
    $count = 0;

} elseif ($action === 'check') {
    $stmt = $pdo->prepare("SELECT count FROM ip_counts WHERE ip = ?");
    $stmt->execute([$ip]);
    $row = $stmt->fetch();
    $count = $row ? (int)$row['count'] : 0;

} else {
    // Default: increment
    $stmt = $pdo->prepare("INSERT INTO ip_counts (ip, count) VALUES (?, 1) ON CONFLICT (ip) DO UPDATE SET count = ip_counts.count + 1 RETURNING count");
    $stmt->execute([$ip]);
    $count = (int)$stmt->fetchColumn();
}

// M2 fix: do NOT return the IP in the response
echo json_encode(['count' => $count]);
