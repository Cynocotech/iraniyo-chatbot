<?php
header('Content-Type: application/json');

require_once __DIR__ . '/cors.php';
checkCors('POST');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

$raw  = file_get_contents('php://input');
$data = json_decode($raw, true);

if (!$data) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid JSON']);
    exit;
}

// Validate and sanitize inputs
$name  = isset($data['name'])  ? trim(strip_tags($data['name']))  : '';
$email = isset($data['email']) ? trim($data['email'])             : '';

$name  = mb_substr($name, 0, 100);
$email = filter_var($email, FILTER_VALIDATE_EMAIL);

if (!$name || !$email) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid name or email']);
    exit;
}

$csv_file = __DIR__ . '/users.csv';
$fp = fopen($csv_file, 'a');

if (!$fp) {
    http_response_code(500);
    echo json_encode(['error' => 'Could not write data']);
    exit;
}

flock($fp, LOCK_EX);

// Write header row if file is new/empty
if (filesize($csv_file) === 0) {
    fputcsv($fp, ['date', 'time', 'name', 'email']);
}

fputcsv($fp, [
    date('Y-m-d'),
    date('H:i:s'),
    $name,
    $email
]);

flock($fp, LOCK_UN);
fclose($fp);

echo json_encode(['success' => true]);
