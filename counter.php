<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

$file = 'ip_counts.json';
$ip = $_SERVER['REMOTE_ADDR'];

// Get current data
if (file_exists($file)) {
    $data = json_decode(file_get_contents($file), true);
} else {
    $data = [];
}

$action = isset($_GET['action']) ? $_GET['action'] : 'increment';

if ($action === 'reset') {
    $data[$ip] = 0;
} elseif ($action === 'check') {
    // Just check, don't increment
} else {
    // Default: increment
    if (!isset($data[$ip])) {
        $data[$ip] = 0;
    }
    $data[$ip]++;
}

// Save back to file
file_put_contents($file, json_encode($data));

// Return count for this IP
echo json_encode([
    'ip' => $ip,
    'count' => isset($data[$ip]) ? $data[$ip] : 0
]);
?>
