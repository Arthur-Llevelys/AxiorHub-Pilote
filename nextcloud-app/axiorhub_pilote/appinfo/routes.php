<?php

declare(strict_types=1);

// Routes nommées : aucune mutation en GET ; les routes métier gardent la protection CSRF (aucun NoCSRFRequired ni PublicPage).
return [
	'routes' => [
		['name' => 'page#index', 'url' => '/', 'verb' => 'GET'],
		['name' => 'config#health', 'url' => '/api/health', 'verb' => 'GET'],
		['name' => 'config#save', 'url' => '/api/config', 'verb' => 'POST'],
		['name' => 'config#saveMatter', 'url' => '/api/admin/matters', 'verb' => 'POST'],
		['name' => 'config#matters', 'url' => '/api/admin/matters', 'verb' => 'GET'],
		['name' => 'config#sync', 'url' => '/api/admin/sync', 'verb' => 'POST'],
		['name' => 'matter#index', 'url' => '/api/matters', 'verb' => 'GET'],
		['name' => 'personal#status', 'url' => '/api/personal', 'verb' => 'GET'],
		['name' => 'personal#link', 'url' => '/api/personal/link', 'verb' => 'POST'],
		['name' => 'personal#test', 'url' => '/api/personal/test', 'verb' => 'GET'],
		['name' => 'personal#unlink', 'url' => '/api/personal/unlink', 'verb' => 'POST'],
		['name' => 'personal#local', 'url' => '/api/personal/local', 'verb' => 'POST'],
		['name' => 'personal#probe', 'url' => '/api/personal/probe', 'verb' => 'POST'],   // 0.4.3 : tester l'adresse avant la liaison   // 0.4.2 : adresse de l'AxiorHub local (lien)
		['name' => 'search#index', 'url' => '/api/search', 'verb' => 'GET'],
		['name' => 'session#create', 'url' => '/api/session', 'verb' => 'POST'],   // 0.4.0 : adresse de connexion à usage unique (interface dans Nextcloud)
		['name' => 'job#index', 'url' => '/api/jobs', 'verb' => 'GET'],
		['name' => 'job#create', 'url' => '/api/jobs', 'verb' => 'POST'],
		['name' => 'job#show', 'url' => '/api/jobs/{id}', 'verb' => 'GET', 'requirements' => ['id' => '[a-f0-9]{32}']],
		['name' => 'job#cancel', 'url' => '/api/jobs/{id}/cancel', 'verb' => 'POST', 'requirements' => ['id' => '[a-f0-9]{32}']],
		['name' => 'job#result', 'url' => '/api/results/{id}', 'verb' => 'GET', 'requirements' => ['id' => '[a-f0-9]{32}']],
		['name' => 'job#approve', 'url' => '/api/results/{id}/approvals', 'verb' => 'POST', 'requirements' => ['id' => '[a-f0-9]{32}']],
	],
];
