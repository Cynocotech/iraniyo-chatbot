<?php

// Fetches the list of enabled agent slugs from the backend, with caching.
function get_allowed_agents(): array {
    $cache_file = __DIR__ . '/.agents.cache.json';
    $cache_ttl = 300; // 5 minutes

    // Check cache
    if (file_exists($cache_file) && (time() - filemtime($cache_file) < $cache_ttl)) {
        $cached_data = json_decode(file_get_contents($cache_file), true);
        if (is_array($cached_data) && !empty($cached_data['slugs'])) {
            return $cached_data['slugs'];
        }
    }

    // DRYAS_BACKEND must be defined by the calling script
    if (!defined('DRYAS_BACKEND')) {
        return ['dr-yas']; // Failsafe
    }

    // Fetch from backend
    $ch = curl_init(DRYAS_BACKEND . '/agents');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 5,
        CURLOPT_SSL_VERIFYPEER => true,
    ]);

    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $curlErr  = curl_error($ch);
    curl_close($ch);

    if ($httpCode !== 200 || !$response) {
        error_log("Agent fetch failed: code=$httpCode, curl_err=$curlErr");
        // If fetch fails, try to use stale cache if it exists
        if (file_exists($cache_file)) {
            $cached_data = json_decode(file_get_contents($cache_file), true);
            if (is_array($cached_data) && !empty($cached_data['slugs'])) {
                return $cached_data['slugs'];
            }
        }
        // Fallback to default if everything fails
        return ['dr-yas', 'trip-planner'];
    }

    $agents = json_decode($response, true);
    if (!is_array($agents)) {
        return ['dr-yas', 'trip-planner']; // Fallback
    }

    $slugs = array_column(array_filter($agents, fn($a) => is_array($a) && !empty($a['slug'])), 'slug');

    if (empty($slugs)) {
        return ['dr-yas', 'trip-planner']; // Fallback
    }

    // Write to cache
    file_put_contents($cache_file, json_encode(['slugs' => $slugs]));

    return $slugs;
}