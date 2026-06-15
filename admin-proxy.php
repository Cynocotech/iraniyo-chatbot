<?php
// ── Proxy: protects /admin and forwards requests to the FastAPI admin panel.
// Browser users authenticate with a session login page here before any
// request is forwarded to the backend.

$secureCookie = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off');
session_set_cookie_params([
    'lifetime' => 0,
    'path' => '/',
    'secure' => $secureCookie,
    'httponly' => true,
    'samesite' => 'Lax',
]);
session_start();

// Override with DRYAS_BACKEND_URL env var if the FastAPI app runs elsewhere.
define('DRYAS_BACKEND', rtrim(getenv('DRYAS_BACKEND_URL') ?: 'http://127.0.0.1:8000', '/'));
define('ADMIN_USER', getenv('IRANIYO_ADMIN_USER') ?: 'admin');
define('ADMIN_PASS', getenv('IRANIYO_ADMIN_PASS') ?: 'IraniyoAdmin2026!');

function currentPath(): string {
    return parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?: '/admin';
}

function isLoggedIn(): bool {
    return !empty($_SESSION['iraniyo_admin_logged_in']);
}

function adminBasePath(): string {
    return str_starts_with(currentPath(), '/admin-proxy.php') ? '/admin-proxy.php' : '/admin';
}

function redirectTo(string $url): never {
    header('Location: ' . $url);
    exit;
}

function loginUrl(): string {
    return adminBasePath();
}

function csrfToken(): string {
    if (empty($_SESSION['iraniyo_admin_csrf'])) {
        $_SESSION['iraniyo_admin_csrf'] = bin2hex(random_bytes(24));
    }
    return $_SESSION['iraniyo_admin_csrf'];
}

function renderLogin(?string $error = null): never {
    $action = htmlspecialchars(loginUrl(), ENT_QUOTES, 'UTF-8');
    $csrf = htmlspecialchars(csrfToken(), ENT_QUOTES, 'UTF-8');
    $errorHtml = $error ? '<div class="error">' . htmlspecialchars($error, ENT_QUOTES, 'UTF-8') . '</div>' : '';

    http_response_code(200);
    header('Content-Type: text/html; charset=UTF-8');
    echo <<<HTML
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ورود مدیریت ایرانیو</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{--bg:#060b14;--panel:rgba(8,14,26,.92);--border:rgba(255,255,255,.12);--primary:#612a80;--primary2:#79359e;--text:#f8fafc;--muted:#94a3b8;--danger:#ef4444}
  body{min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at top right,rgba(97,42,128,.38),transparent 34%),linear-gradient(135deg,#060b14,#0d0b2e);font-family:Tahoma,'Segoe UI',Arial,sans-serif;color:var(--text);padding:20px}
  .card{width:100%;max-width:390px;background:var(--panel);border:1px solid var(--border);border-radius:18px;padding:28px 24px;box-shadow:0 30px 70px rgba(0,0,0,.45);backdrop-filter:blur(16px)}
  .brand{text-align:center;margin-bottom:22px}
  .brand img{width:150px;max-width:65%;background:#fff;border-radius:12px;padding:8px 14px;margin-bottom:14px}
  h1{font-size:18px;margin-bottom:6px}
  p{font-size:13px;color:var(--muted);line-height:1.8}
  label{display:block;font-size:12px;color:var(--muted);margin:14px 0 7px;text-align:right}
  input{width:100%;height:44px;border-radius:12px;border:1px solid var(--border);background:rgba(255,255,255,.06);color:var(--text);padding:0 14px;font-size:14px;outline:none;direction:ltr;text-align:left}
  input:focus{border-color:var(--primary2);box-shadow:0 0 0 3px rgba(121,53,158,.22)}
  button{width:100%;height:45px;margin-top:18px;border:0;border-radius:12px;background:linear-gradient(135deg,var(--primary),var(--primary2));color:#fff;font-weight:700;font-size:14px;cursor:pointer}
  button:hover{filter:brightness(1.08)}
  .error{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.45);color:#fecaca;border-radius:12px;padding:10px 12px;font-size:12px;line-height:1.7;margin-bottom:14px;text-align:center}
  .foot{margin-top:16px;text-align:center;font-size:11px;color:var(--muted)}
</style>
</head>
<body>
  <form class="card" method="post" action="$action" autocomplete="off">
    <div class="brand">
      <img src="https://panel.cybercina.co.uk//storage/logos/N0yQlVchcj4ucrQfVJwbXXB13FhWTMFccUBmWLpI.png" alt="Iraniyo">
      <h1>ورود پنل مدیریت</h1>
      <p>برای مدیریت دستیارها، تنظیمات و گزارش گفتگوها وارد شوید.</p>
    </div>
    $errorHtml
    <input type="hidden" name="_action" value="login">
    <input type="hidden" name="_csrf" value="$csrf">
    <label for="username">نام کاربری</label>
    <input id="username" name="username" type="text" required autofocus>
    <label for="password">رمز عبور</label>
    <input id="password" name="password" type="password" required>
    <button type="submit">ورود به پنل</button>
    <div class="foot">Iraniyo Admin</div>
  </form>
</body>
</html>
HTML;
    exit;
}

function handleLoginPost(): void {
    if (($_POST['_action'] ?? '') !== 'login') {
        return;
    }

    if (!hash_equals($_SESSION['iraniyo_admin_csrf'] ?? '', $_POST['_csrf'] ?? '')) {
        renderLogin('درخواست نامعتبر است. لطفاً دوباره تلاش کنید.');
        return;
    }

    $user = (string) ($_POST['username'] ?? '');
    $pass = (string) ($_POST['password'] ?? '');
    if (!hash_equals(ADMIN_USER, $user) || !hash_equals(ADMIN_PASS, $pass)) {
        renderLogin('نام کاربری یا رمز عبور اشتباه است.');
        return;
    }

    session_regenerate_id(true);
    $_SESSION['iraniyo_admin_logged_in'] = true;
    $_SESSION['iraniyo_admin_csrf'] = bin2hex(random_bytes(24));
    redirectTo(loginUrl());
}

function handleLogout(): void {
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $params = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000, $params['path'], $params['domain'] ?? '', $params['secure'], $params['httponly']);
    }
    session_destroy();
    redirectTo(loginUrl());
}

if (isset($_GET['logout'])) {
    handleLogout();
}
handleLoginPost();
if (!isLoggedIn()) {
    renderLogin();
}

$method = $_SERVER['REQUEST_METHOD'];
$originalPath = currentPath();
$path = $originalPath;
if ($path === '/admin-proxy.php') {
    $path = '/admin';
} elseif (str_starts_with($path, '/admin-proxy.php/')) {
    $path = '/admin' . substr($path, strlen('/admin-proxy.php'));
}
$query = [];
parse_str(parse_url($_SERVER['REQUEST_URI'], PHP_URL_QUERY) ?: '', $query);

$target = DRYAS_BACKEND . $path . ($query ? '?' . http_build_query($query) : '');

$ch = curl_init($target);
$opts = [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HEADER         => true,
    CURLOPT_TIMEOUT        => 30,
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_CUSTOMREQUEST  => $method,
];

if ($method === 'POST' || $method === 'PUT' || $method === 'PATCH') {
    $contentType = $_SERVER['CONTENT_TYPE'] ?? 'application/json';
    $opts[CURLOPT_POSTFIELDS] = file_get_contents('php://input');
    $opts[CURLOPT_HTTPHEADER] = ['Content-Type: ' . $contentType];
}

curl_setopt_array($ch, $opts);
$raw     = curl_exec($ch);
$curlErr = curl_error($ch);

if ($curlErr) {
    http_response_code(502);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Upstream error', 'detail' => $curlErr]);
    exit;
}

$httpCode   = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$headerSize = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
$contentType = curl_getinfo($ch, CURLINFO_CONTENT_TYPE);

$body = substr($raw, $headerSize);
$rawHeaders = substr($raw, 0, $headerSize);

if ($contentType && stripos($contentType, 'text/html') !== false) {
    $base = adminBasePath();
    if ($base === '/admin-proxy.php') {
        $body = str_replace('/admin/', '/admin-proxy.php/', $body);
        $body = str_replace("'/admin/", "'/admin-proxy.php/", $body);
        $body = str_replace('"/admin/', '"/admin-proxy.php/', $body);
    }
    // The original logout link injection was brittle. A more robust method is to
    // inject a styled link just before the closing </body> tag.
    $logout = htmlspecialchars($base . '?logout=1', ENT_QUOTES, 'UTF-8');
    $logoutLink = '<a href="' . $logout . '" style="position: fixed; top: 1rem; right: 1rem; z-index: 9999; background: #6f2c91; color: white; padding: 0.5rem 1rem; border-radius: 8px; font-family: sans-serif; text-decoration: none; box-shadow: 0 2px 10px rgba(0,0,0,0.3);">خروج از پنل</a>';
    $body = str_ireplace('</body>', $logoutLink . '</body>', $body);
}

http_response_code($httpCode ?: 200);
if ($contentType) {
    header('Content-Type: ' . $contentType);
}
// Forward the file-download header for CSV export endpoints.
if (preg_match('/^Content-Disposition:.*$/mi', $rawHeaders, $m)) {
    header(trim($m[0]));
}
echo $body;
