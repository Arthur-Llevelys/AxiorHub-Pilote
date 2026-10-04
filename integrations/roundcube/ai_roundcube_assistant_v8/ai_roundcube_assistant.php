<?php

class ai_roundcube_assistant extends rcube_plugin
{
    public $task = 'mail|ai_assistant';

    private $rcmail;

    public function init()
    {
        $this->rcmail = rcmail::get_instance();

        // Older installations exposed a separate, incomplete task at
        // ?_task=ai_assistant. The maintained assistant now lives in the mail
        // toolbar, where the selected message and session are available.
        if ($this->rcmail->task === 'ai_assistant') {
            $this->register_action('index', [$this, 'legacy_assistant_redirect']);
            return;
        }

        $this->load_config();
        $this->include_script('ai_roundcube_assistant.js');
        $this->include_stylesheet('ai_roundcube_assistant.css');

        $this->register_action('plugin.ai_get_message', [$this, 'get_message']);
        $this->register_action(
            'plugin.ai_axiorhub_completion',
            [$this, 'axiorhub_completion']
        );
        $this->register_action('plugin.ai_save_project', [$this, 'save_project']);
        $this->register_action(
            'plugin.ai_invoice_ninja_projects',
            [$this, 'invoice_ninja_projects']
        );
        $this->register_action(
            'plugin.ai_invoice_ninja_time',
            [$this, 'invoice_ninja_time']
        );
        $this->register_action(
            'plugin.ai_download_attachment',
            [$this, 'download_attachment']
        );
        $this->register_action(
            'plugin.ai_nextcloud_file',
            [$this, 'nextcloud_file']
        );
        $this->register_action(
            'plugin.ai_download_archive',
            [$this, 'download_archive']
        );
        $this->register_action(
            'plugin.ai_v8_action',
            [$this, 'v8_action']
        );
        $this->register_action(
            'plugin.ai_v8_health',
            [$this, 'v8_health']
        );
        $this->register_action(
            'plugin.ai_v8_nextcloud_browse',
            [$this, 'v8_nextcloud_browse']
        );
        $this->register_action(
            'plugin.ai_v8_nextcloud_events',
            [$this, 'v8_nextcloud_events']
        );

        $this->add_button([
            'command'  => 'plugin.ai_menu',
            'type'     => 'link',
            'label'    => 'IA',
            'title'    => 'Ouvrir le menu IA',
            'class'    => 'button reply disabled',
            'classact' => 'button reply',
        ], 'toolbar');
    }

    public function legacy_assistant_redirect()
    {
        header('Cache-Control: no-store');
        header('Location: ?_task=mail', true, 303);
        exit;
    }

    public function get_message()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->send_error('Session Roundcube absente.');
        }

        $uid = rcube_utils::get_input_string('_uid', rcube_utils::INPUT_POST);
        $mailbox = rcube_utils::get_input_string('_mbox', rcube_utils::INPUT_POST);

        if (!$uid || str_contains($uid, ',')) {
            $this->send_error('Sélectionnez un seul courriel.');
        }

        if (!$mailbox) {
            $mailbox = $this->rcmail->storage->get_folder();
        }

        try {
            $message = new rcube_message($uid, $mailbox);
            $headers = $message->headers;

            $subject = trim((string) ($headers->subject ?? ''));
            $from = trim((string) ($headers->from ?? ''));
            $to = trim((string) ($headers->to ?? ''));
            $cc = trim((string) ($headers->cc ?? ''));
            $bcc = trim((string) ($headers->bcc ?? ''));
            $date = trim((string) ($headers->date ?? ''));
            $message_id = trim((string) (
                $headers->messageID
                ?? $headers->message_id
                ?? ''
            ));
            $body = trim((string) $message->first_text_part());

            if ($body === '') {
                $this->send_error('Le texte de ce courriel ne peut pas être extrait.');
            }

            $body = html_entity_decode(
                strip_tags(str_ireplace(
                    ['<br>', '<br/>', '<br />', '</p>', '</div>'],
                    "\n",
                    $body
                )),
                ENT_QUOTES | ENT_HTML5,
                'UTF-8'
            );

            $body = preg_replace("/\r\n?|\x{2028}|\x{2029}/u", "\n", $body);
            $body = preg_replace("/\n{4,}/", "\n\n\n", $body);

            $max_chars = (int) $this->rcmail->config->get(
                'ai_max_message_chars',
                30000
            );

            $body = mb_substr(trim($body), 0, $max_chars);
            $raw_key = $message_id !== ''
                ? strtolower($message_id)
                : $mailbox . ':' . $uid;
            $message_key = hash('sha256', $raw_key);

            $prefs = $this->rcmail->user->get_prefs();
            $projects = is_array($prefs['ai_roundcube_projects'] ?? null)
                ? $prefs['ai_roundcube_projects']
                : [];
            $project = $projects[$message_key] ?? null;

            $attachments = [];
            foreach (($message->attachments ?? []) as $attachment) {
                $name = trim((string) (
                    $attachment->filename
                    ?? $attachment->name
                    ?? ''
                ));
                $part_id = trim((string) ($attachment->mime_id ?? ''));
                if ($name !== '' && $part_id !== '') {
                    $attachments[] = [
                        'part_id' => $part_id,
                        'name' => $name,
                        'mimetype' => trim((string) (
                            $attachment->mimetype
                            ?? 'application/octet-stream'
                        )),
                        'size' => max(0, (int) ($attachment->size ?? 0)),
                    ];
                }
            }

            $this->rcmail->output->command('plugin.ai_message_ready', [
                'key' => $message_key,
                'uid' => (string) $uid,
                'mailbox' => (string) $mailbox,
                'subject' => $subject,
                'from' => $from,
                'to' => $to,
                'cc' => $cc,
                'bcc' => $bcc,
                'date' => $date,
                'body' => $body,
                'attachments' => $attachments,
                'project' => $project,
            ]);

            $this->rcmail->output->send();
        } catch (Throwable $error) {
            rcube::raise_error([
                'code' => 600,
                'type' => 'php',
                'message' => 'AI message extraction failed: ' . $error->getMessage(),
            ], true, false);

            $this->send_error(
                'La lecture du courriel a échoué. Consultez le journal Roundcube.'
            );
        }
    }

    public function axiorhub_completion()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->json_error('Session Roundcube absente.', 401);
        }
        $action = trim(rcube_utils::get_input_string('_ai_action', rcube_utils::INPUT_POST));
        $raw_messages = rcube_utils::get_input_string('_messages', rcube_utils::INPUT_POST);
        $allowed = [
            'reply', 'analyse', 'summary', 'translate', 'deadlines', 'chronology',
            'missing', 'ack', 'arguments', 'procedure', 'citations_extract',
            'call_prep', 'call_report', 'compare', 'anonymize', 'archive_summary',
            'event_suggest', 'automation_suggest', 'project_synthesis_prepare',
            'project_overview_prepare',
        ];
        if (!in_array($action, $allowed, true)) {
            $this->json_error('Action IA Roundcube non autorisée.', 400);
        }
        if ($raw_messages === '' || strlen($raw_messages) > 130000) {
            $this->json_error('Contexte IA Roundcube absent ou trop long.', 413);
        }
        try {
            $messages = json_decode($raw_messages, true, 32, JSON_THROW_ON_ERROR);
        } catch (Throwable $error) {
            $this->json_error('Messages IA Roundcube invalides.', 400);
        }
        if (!is_array($messages) || count($messages) < 1 || count($messages) > 6) {
            $this->json_error('Nombre de messages IA Roundcube invalide.', 400);
        }
        $url = trim((string) $this->rcmail->config->get(
            'ai_axiorhub_api_url',
            'https://courriel.example.com/agent-courriel/api/v1/ai/chat/completions'
        ));
        $parts = parse_url($url);
        if (!is_array($parts) || strtolower((string) ($parts['scheme'] ?? '')) !== 'https'
            || !isset($parts['host']) || isset($parts['user']) || isset($parts['pass'])
            || isset($parts['query']) || isset($parts['fragment'])
            || !str_ends_with((string) ($parts['path'] ?? ''), '/agent-courriel/api/v1/ai/chat/completions')) {
            $this->json_error('Adresse API AxiorHub refusée.', 500);
        }
        $secret_file = trim((string) $this->rcmail->config->get(
            'ai_axiorhub_token_file',
            '/etc/roundcube/axiorhub-api.token'
        ));
        if ($secret_file === '' || !is_readable($secret_file)) {
            $this->json_error('Relais AxiorHub non configuré : jeton serveur absent.', 503);
        }
        $perms = fileperms($secret_file);
        if ($perms === false || (($perms & 0007) !== 0) || (($perms & 0022) !== 0)) {
            $this->json_error('Permissions du jeton AxiorHub trop larges.', 503);
        }
        $token_raw = (string) file_get_contents($secret_file);
        if (substr_count($token_raw, "\n") > 1 || str_contains(rtrim($token_raw, "\r\n"), "\n")
            || str_contains(rtrim($token_raw, "\r\n"), "\r")) {
            $this->json_error('Jeton AxiorHub invalide.', 503);
        }
        $token = trim($token_raw);
        if (strlen($token) < 40) {
            $this->json_error('Jeton AxiorHub invalide.', 503);
        }
        $payload = json_encode([
            'action' => $action,
            'messages' => $messages,
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $curl = curl_init($url);
        if ($curl === false) {
            $this->json_error('Initialisation du relais AxiorHub impossible.', 503);
        }
        curl_setopt_array($curl, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_HTTPHEADER => [
                'Accept: application/json',
                'Content-Type: application/json',
                'Authorization: Bearer ' . $token,
            ],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 8,
            CURLOPT_TIMEOUT => 240,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_MAXREDIRS => 0,
            CURLOPT_PROTOCOLS => CURLPROTO_HTTPS,
        ]);
        $raw = curl_exec($curl);
        $error = curl_error($curl);
        $status = (int) curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);
        if ($raw === false || $error !== '') {
            $this->log_integration_error('Relais AxiorHub', new RuntimeException($error));
            $this->json_error('API AxiorHub injoignable depuis Roundcube.', 502);
        }
        try {
            $result = json_decode((string) $raw, true, 64, JSON_THROW_ON_ERROR);
        } catch (Throwable $error) {
            $this->json_error('Réponse AxiorHub invalide.', 502);
        }
        if ($status < 200 || $status >= 300 || !is_array($result)) {
            $code = is_array($result) ? trim((string) ($result['error'] ?? '')) : '';
            $this->json_error('AxiorHub a refusé le traitement' . ($code !== '' ? ' : ' . $code : '.') , 502);
        }
        $this->json_response($result);
    }

    public function save_project()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->send_error('Session Roundcube absente.');
        }

        $key = trim(rcube_utils::get_input_string('_key', rcube_utils::INPUT_POST));
        $folder_id = trim(rcube_utils::get_input_string(
            '_folder_id',
            rcube_utils::INPUT_POST
        ));
        $folder_name = trim(rcube_utils::get_input_string(
            '_folder_name',
            rcube_utils::INPUT_POST
        ));
        $chat_id = trim(rcube_utils::get_input_string(
            '_chat_id',
            rcube_utils::INPUT_POST
        ));
        $file_ids_json = rcube_utils::get_input_string(
            '_file_ids',
            rcube_utils::INPUT_POST
        );
        $file_ids = json_decode($file_ids_json ?: '[]', true);
        if (!is_array($file_ids)) {
            $file_ids = [];
        }
        $file_ids = array_values(array_unique(array_filter(array_map(
            static fn($id) => mb_substr(trim((string) $id), 0, 200),
            array_slice($file_ids, 0, 50)
        ))));
        $note_ids_json = rcube_utils::get_input_string(
            '_note_ids',
            rcube_utils::INPUT_POST
        );
        $note_ids = json_decode($note_ids_json ?: '[]', true);
        if (!is_array($note_ids)) {
            $note_ids = [];
        }
        $note_ids = array_values(array_unique(array_filter(array_map(
            static fn($id) => mb_substr(trim((string) $id), 0, 200),
            array_slice($note_ids, 0, 100)
        ))));

        if (!preg_match('/^[a-f0-9]{64}$/', $key)) {
            $this->send_error('Identifiant de courriel invalide.');
        }

        $prefs = $this->rcmail->user->get_prefs();
        $projects = is_array($prefs['ai_roundcube_projects'] ?? null)
            ? $prefs['ai_roundcube_projects']
            : [];

        if ($folder_id === '') {
            unset($projects[$key]);
        } else {
            $projects[$key] = [
                'folder_id' => mb_substr($folder_id, 0, 200),
                'folder_name' => mb_substr($folder_name, 0, 300),
                'chat_id' => mb_substr($chat_id, 0, 200),
                'file_ids' => $file_ids,
                'note_ids' => $note_ids,
                'updated_at' => time(),
            ];
        }

        if (count($projects) > 500) {
            uasort($projects, static function ($a, $b) {
                return ($b['updated_at'] ?? 0) <=> ($a['updated_at'] ?? 0);
            });
            $projects = array_slice($projects, 0, 500, true);
        }

        if (!$this->rcmail->user->save_prefs([
            'ai_roundcube_projects' => $projects,
        ])) {
            $this->send_error('Le rattachement au projet n’a pas pu être enregistré.');
        }

        $this->rcmail->output->command('plugin.ai_project_saved', [
            'key' => $key,
            'folder_id' => $folder_id,
            'folder_name' => $folder_name,
            'chat_id' => $chat_id,
            'file_ids' => $file_ids,
            'note_ids' => $note_ids,
        ]);
        $this->rcmail->output->send();
    }

    public function invoice_ninja_projects()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->json_error('Session Roundcube absente.', 401);
        }

        $folder_id = trim(rcube_utils::get_input_string(
            '_folder_id',
            rcube_utils::INPUT_POST
        ));
        if ($folder_id === '' || mb_strlen($folder_id) > 200) {
            $this->json_error('Projet Open WebUI invalide.', 400);
        }

        try {
            $response = $this->invoice_ninja_request(
                'GET',
                '/api/v1/projects?per_page=100&include=client&sort=name%7Casc'
            );
            $items = is_array($response['data'] ?? null)
                ? $response['data']
                : [];
            $projects = [];
            foreach ($items as $item) {
                if (!is_array($item)) {
                    continue;
                }
                $id = trim((string) ($item['id'] ?? ''));
                if ($id === '') {
                    continue;
                }
                $client = is_array($item['client'] ?? null)
                    ? $item['client']
                    : [];
                $client_name = trim((string) (
                    $client['display_name']
                    ?? $client['name']
                    ?? ''
                ));
                $projects[] = [
                    'id' => mb_substr($id, 0, 200),
                    'name' => mb_substr(trim((string) (
                        $item['name']
                        ?? $item['number']
                        ?? $id
                    )), 0, 300),
                    'number' => mb_substr(trim((string) (
                        $item['number']
                        ?? ''
                    )), 0, 100),
                    'client_id' => mb_substr(trim((string) (
                        $item['client_id']
                        ?? $client['id']
                        ?? ''
                    )), 0, 200),
                    'client_name' => mb_substr($client_name, 0, 300),
                ];
            }

            $prefs = $this->rcmail->user->get_prefs();
            $mappings = is_array($prefs['ai_roundcube_invoice_mappings'] ?? null)
                ? $prefs['ai_roundcube_invoice_mappings']
                : [];

            $this->json_response([
                'configured' => true,
                'projects' => $projects,
                'mapping' => is_array($mappings[$folder_id] ?? null)
                    ? $mappings[$folder_id]
                    : null,
            ]);
        } catch (Throwable $error) {
            $this->log_integration_error('Invoice Ninja project listing failed', $error);
            $this->json_error(
                'Connexion à Invoice Ninja impossible. Vérifiez la configuration serveur.',
                502
            );
        }
    }

    public function invoice_ninja_time()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->json_error('Session Roundcube absente.', 401);
        }

        $folder_id = trim(rcube_utils::get_input_string(
            '_folder_id',
            rcube_utils::INPUT_POST
        ));
        $project_id = trim(rcube_utils::get_input_string(
            '_project_id',
            rcube_utils::INPUT_POST
        ));
        $project_name = trim(rcube_utils::get_input_string(
            '_project_name',
            rcube_utils::INPUT_POST
        ));
        $client_id = trim(rcube_utils::get_input_string(
            '_client_id',
            rcube_utils::INPUT_POST
        ));
        $client_name = trim(rcube_utils::get_input_string(
            '_client_name',
            rcube_utils::INPUT_POST
        ));
        $description = trim(rcube_utils::get_input_string(
            '_description',
            rcube_utils::INPUT_POST
        ));
        $start_value = trim(rcube_utils::get_input_string(
            '_start',
            rcube_utils::INPUT_POST
        ));
        $end_value = trim(rcube_utils::get_input_string(
            '_end',
            rcube_utils::INPUT_POST
        ));
        $billable_value = trim(rcube_utils::get_input_string(
            '_billable',
            rcube_utils::INPUT_POST
        ));
        $entry_uuid = trim(rcube_utils::get_input_string(
            '_entry_uuid',
            rcube_utils::INPUT_POST
        ));
        $action_id = trim(rcube_utils::get_input_string(
            '_action_id',
            rcube_utils::INPUT_POST
        ));

        if ($folder_id === '' || mb_strlen($folder_id) > 200) {
            $this->json_error('Projet Open WebUI invalide.', 400);
        }
        if (!preg_match('/^[A-Za-z0-9_-]{1,200}$/', $project_id)) {
            $this->json_error('Projet Invoice Ninja invalide.', 400);
        }
        if ($client_id !== '' && !preg_match('/^[A-Za-z0-9_-]{1,200}$/', $client_id)) {
            $this->json_error('Client Invoice Ninja invalide.', 400);
        }
        if (!preg_match(
            '/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i',
            $entry_uuid
        )) {
            $this->json_error('Identifiant de saisie de temps invalide.', 400);
        }
        if (!$this->v8_is_uuid($action_id)) {
            $this->json_error('Identifiant d’audit invalide.', 400);
        }
        try {
            $audit_db = $this->v8_db();
            $audit_stmt = $audit_db->prepare(
                "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running'"
            );
            $audit_stmt->execute([':id' => $action_id, ':user_id' => (string) $this->rcmail->user->ID]);
            if (!$audit_stmt->fetchColumn()) {
                $this->json_error('Cette écriture Invoice Ninja n’a pas été confirmée.', 409);
            }
        } catch (Throwable $error) {
            $this->log_integration_error('Invoice Ninja audit check failed', $error);
            $this->json_error('Journal V8 indisponible : écriture Invoice Ninja bloquée.', 503);
        }
        if (!ctype_digit($start_value) || !ctype_digit($end_value)) {
            $this->json_error('Horodatage de temps invalide.', 400);
        }

        $start = (int) $start_value;
        $end = (int) $end_value;
        if ($start <= 0 || $end <= $start || ($end - $start) > 604800) {
            $this->json_error('La durée doit être comprise entre une seconde et sept jours.', 400);
        }
        if ($description === '') {
            $this->json_error('La description du temps passé est obligatoire.', 400);
        }
        $description = mb_substr($description, 0, 1000);
        $billable = in_array(strtolower($billable_value), ['1', 'true', 'yes', 'oui'], true);

        $prefs = $this->rcmail->user->get_prefs();
        $entries = is_array($prefs['ai_roundcube_invoice_entries'] ?? null)
            ? $prefs['ai_roundcube_invoice_entries']
            : [];
        if (isset($entries[$entry_uuid])) {
            $this->json_response([
                'created' => false,
                'duplicate' => true,
                'task_id' => (string) ($entries[$entry_uuid]['task_id'] ?? ''),
                'message' => 'Cette durée avait déjà été enregistrée.',
            ]);
        }

        try {
            $payload = [
                'project_id' => $project_id,
                'description' => $description,
                'time_log' => [[
                    $start,
                    $end,
                    $description,
                    $billable,
                ]],
            ];
            if ($client_id !== '') {
                $payload['client_id'] = $client_id;
            }
            $response = $this->invoice_ninja_request(
                'POST',
                '/api/v1/tasks',
                $payload
            );
            $task = is_array($response['data'] ?? null)
                ? $response['data']
                : $response;
            $task_id = trim((string) ($task['id'] ?? ''));
            if ($task_id === '') {
                throw new RuntimeException('Invoice Ninja n’a renvoyé aucun identifiant de tâche.');
            }

            $mappings = is_array($prefs['ai_roundcube_invoice_mappings'] ?? null)
                ? $prefs['ai_roundcube_invoice_mappings']
                : [];
            $mappings[$folder_id] = [
                'project_id' => $project_id,
                'project_name' => mb_substr($project_name, 0, 300),
                'client_id' => mb_substr($client_id, 0, 200),
                'client_name' => mb_substr($client_name, 0, 300),
                'updated_at' => time(),
            ];
            $entries[$entry_uuid] = [
                'task_id' => mb_substr($task_id, 0, 200),
                'folder_id' => mb_substr($folder_id, 0, 200),
                'created_at' => time(),
            ];
            if (count($entries) > 500) {
                uasort($entries, static function ($a, $b) {
                    return ($b['created_at'] ?? 0) <=> ($a['created_at'] ?? 0);
                });
                $entries = array_slice($entries, 0, 500, true);
            }

            if (!$this->rcmail->user->save_prefs([
                'ai_roundcube_invoice_mappings' => $mappings,
                'ai_roundcube_invoice_entries' => $entries,
            ])) {
                rcube::raise_error([
                    'code' => 600,
                    'type' => 'php',
                    'message' => 'Invoice Ninja task created but local idempotency state was not saved.',
                ], true, false);
            }

            $this->v8_audit($audit_db, [
                'user_id' => (string) $this->rcmail->user->ID,
                'folder_id' => $folder_id,
                'message_key' => '',
                'correlation_id' => $action_id,
                'action' => 'invoice_ninja_time_create',
                'phase' => 'remote_created',
                'target' => 'invoice_ninja',
                'object_type' => 'task',
                'object_id' => $task_id,
                'status' => 'succeeded',
                'details' => [
                    'entry_uuid' => $entry_uuid,
                    'project_id' => $project_id,
                    'duration_seconds' => $end - $start,
                    'billable' => $billable,
                ],
            ]);

            $this->json_response([
                'created' => true,
                'duplicate' => false,
                'task_id' => $task_id,
                'project_id' => $project_id,
                'duration_seconds' => $end - $start,
            ]);
        } catch (Throwable $error) {
            $this->log_integration_error('Invoice Ninja time creation failed', $error);
            $this->json_error(
                'L’enregistrement dans Invoice Ninja a échoué. Aucune réussite ne peut être confirmée.',
                502
            );
        }
    }

    public function nextcloud_file()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->attachment_error('Session Roundcube absente.', 401);
        }

        $node_id = trim(rcube_utils::get_input_string('_node_id', rcube_utils::INPUT_POST));
        try {
            if ($node_id === '') {
                throw new RuntimeException('Un identifiant de nœud signé est obligatoire.');
            }
            $node = $this->v8_verify_node($node_id);
            if (($node['type'] ?? '') !== 'file') {
                throw new RuntimeException('Le nœud demandé n’est pas un fichier.');
            }
            $path = $this->v8_assert_allowed_file_path((string) $node['path']);
        } catch (Throwable $error) {
            $this->attachment_error('Chemin Nextcloud refusé par la liste blanche.', 403);
        }
        if (str_ends_with($path, '/')) {
            $this->attachment_error('Chemin Nextcloud invalide.', 400);
        }
        $segments = explode('/', $path);

        try {
            $base_url = rtrim(trim((string) $this->rcmail->config->get(
                'ai_nextcloud_url',
                ''
            )), '/');
            $parts = parse_url($base_url);
            if (
                $base_url === '' || !filter_var($base_url, FILTER_VALIDATE_URL) ||
                !is_array($parts) || strtolower((string) ($parts['scheme'] ?? '')) !== 'https' ||
                isset($parts['user']) || isset($parts['pass']) || isset($parts['query']) ||
                isset($parts['fragment'])
            ) {
                throw new RuntimeException('URL Nextcloud absente ou non conforme.');
            }

            $username = trim((string) $this->rcmail->config->get(
                'ai_nextcloud_username',
                ''
            ));
            if ($username === '' || mb_strlen($username) > 255 || preg_match('/[\r\n]/', $username)) {
                throw new RuntimeException('Compte Nextcloud dédié absent ou invalide.');
            }
            $password_file = trim((string) $this->rcmail->config->get(
                'ai_nextcloud_password_file',
                '/etc/roundcube/ai-nextcloud.token'
            ));
            if ($password_file === '' || !is_file($password_file) || !is_readable($password_file)) {
                throw new RuntimeException('Fichier du mot de passe d’application Nextcloud absent ou illisible.');
            }
            $password_perms = fileperms($password_file);
            if ($password_perms === false || (($password_perms & 0007) !== 0) || (($password_perms & 0022) !== 0)) {
                throw new RuntimeException('Permissions du secret Nextcloud trop larges.');
            }
            $password_size = filesize($password_file);
            if ($password_size === false || $password_size < 8 || $password_size > 4096) {
                throw new RuntimeException('Fichier du mot de passe Nextcloud invalide.');
            }
            $password = trim((string) file_get_contents($password_file));
            if ($password === '' || preg_match('/[\r\n]/', $password)) {
                throw new RuntimeException('Mot de passe d’application Nextcloud invalide.');
            }

            $origin = 'https://' . $parts['host'];
            if (isset($parts['port'])) {
                $origin .= ':' . (int) $parts['port'];
            }
            $base_path = rtrim((string) ($parts['path'] ?? ''), '/');
            $encoded_path = implode('/', array_map('rawurlencode', $segments));
            $url = $origin . $base_path . '/remote.php/dav/files/' .
                rawurlencode($username) . '/' . $encoded_path;
            $max_bytes = min(26214400, max(1048576, (int) $this->rcmail->config->get(
                'ai_max_attachment_bytes',
                26214400
            )));
            $content = '';
            $too_large = false;
            $curl = curl_init($url);
            if ($curl === false) {
                throw new RuntimeException('Initialisation cURL impossible.');
            }
            $options = [
                CURLOPT_HTTPGET => true,
                CURLOPT_RETURNTRANSFER => false,
                CURLOPT_CONNECTTIMEOUT => 10,
                CURLOPT_TIMEOUT => max(10, min(120, (int) $this->rcmail->config->get(
                    'ai_nextcloud_timeout',
                    60
                ))),
                CURLOPT_FOLLOWLOCATION => false,
                CURLOPT_MAXREDIRS => 0,
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_SSL_VERIFYHOST => 2,
                CURLOPT_HTTPAUTH => CURLAUTH_BASIC,
                CURLOPT_USERPWD => $username . ':' . $password,
                CURLOPT_HTTPHEADER => ['Accept: application/octet-stream'],
                CURLOPT_WRITEFUNCTION => static function ($handle, string $chunk) use (
                    &$content,
                    &$too_large,
                    $max_bytes
                ): int {
                    if (strlen($content) + strlen($chunk) > $max_bytes) {
                        $too_large = true;
                        return 0;
                    }
                    $content .= $chunk;
                    return strlen($chunk);
                },
            ];
            if (defined('CURLOPT_PROTOCOLS') && defined('CURLPROTO_HTTPS')) {
                $options[CURLOPT_PROTOCOLS] = CURLPROTO_HTTPS;
            }
            curl_setopt_array($curl, $options);
            $ok = curl_exec($curl);
            $curl_error = curl_error($curl);
            $http_code = (int) curl_getinfo($curl, CURLINFO_HTTP_CODE);
            $content_type = trim((string) curl_getinfo($curl, CURLINFO_CONTENT_TYPE));
            curl_close($curl);

            if ($too_large) {
                throw new RuntimeException('Fichier Nextcloud trop volumineux.');
            }
            if ($ok === false || $curl_error !== '') {
                throw new RuntimeException('Téléchargement WebDAV impossible: ' . $curl_error);
            }
            if ($http_code < 200 || $http_code >= 300) {
                throw new RuntimeException('Nextcloud WebDAV HTTP ' . $http_code);
            }
            if ($content === '') {
                throw new RuntimeException('Le fichier Nextcloud est vide.');
            }

            if (!class_exists('finfo')) {
                throw new RuntimeException('Extension PHP fileinfo absente.');
            }
            $detected_type = (string) (new finfo(FILEINFO_MIME_TYPE))->buffer($content);
            $dangerous_types = [
                'application/x-dosexec', 'application/x-executable', 'application/x-elf',
                'application/x-httpd-php', 'application/x-php', 'application/x-sh',
                'text/x-php', 'text/x-shellscript', 'text/javascript', 'application/javascript',
            ];
            if ($detected_type === '' || in_array(strtolower($detected_type), $dangerous_types, true)) {
                throw new RuntimeException('Type MIME réel interdit.');
            }
            $content_type = $detected_type;

            $filename = $this->safe_archive_filename((string) end($segments));
            if (!preg_match(
                '~^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$~i',
                $content_type
            )) {
                $content_type = 'application/octet-stream';
            }
            header('Content-Type: ' . $content_type);
            header('Content-Length: ' . strlen($content));
            header('Cache-Control: private, no-store, max-age=0');
            header('X-Content-Type-Options: nosniff');
            header('Content-Security-Policy: sandbox');
            header('X-AI-Filename: ' . rawurlencode($filename));
            header("Content-Disposition: attachment; filename*=UTF-8''" . rawurlencode($filename));
            echo $content;
            exit;
        } catch (Throwable $error) {
            $this->log_integration_error('Nextcloud WebDAV file import failed', $error);
            $this->attachment_error(
                'Import Nextcloud impossible. Vérifiez la configuration serveur et le chemin du fichier.',
                502
            );
        }
    }

    public function download_attachment()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->attachment_error('Session Roundcube absente.', 401);
        }

        $uid = rcube_utils::get_input_string('_uid', rcube_utils::INPUT_GET);
        $mailbox = rcube_utils::get_input_string('_mbox', rcube_utils::INPUT_GET);
        $part_id = rcube_utils::get_input_string('_part', rcube_utils::INPUT_GET);

        if (!$uid || str_contains($uid, ',') || $part_id === '') {
            $this->attachment_error('Pièce jointe invalide.', 400);
        }

        if (!$mailbox) {
            $mailbox = $this->rcmail->storage->get_folder();
        }

        try {
            $message = new rcube_message($uid, $mailbox);
            $attachment = $message->attachments[$part_id] ?? null;
            if (!$attachment) {
                $this->attachment_error('Pièce jointe introuvable.', 404);
            }

            $max_bytes = (int) $this->rcmail->config->get(
                'ai_max_attachment_bytes',
                26214400
            );
            $declared_size = max(0, (int) ($attachment->size ?? 0));
            if ($declared_size > $max_bytes) {
                $this->attachment_error(
                    'Pièce jointe trop volumineuse (maximum 25 Mo).',
                    413
                );
            }

            $content = $message->get_part_content(
                $part_id,
                null,
                true,
                $max_bytes + 1,
                false
            );
            if (!is_string($content)) {
                $this->attachment_error('Lecture de la pièce jointe impossible.', 500);
            }
            if (strlen($content) > $max_bytes) {
                $this->attachment_error(
                    'Pièce jointe trop volumineuse (maximum 25 Mo).',
                    413
                );
            }

            $filename = trim((string) (
                $attachment->filename
                ?? $attachment->name
                ?? 'piece-jointe'
            ));
            $filename = str_replace(["\r", "\n", '/', '\\'], '_', $filename);
            $mimetype = trim((string) (
                $attachment->mimetype
                ?? 'application/octet-stream'
            ));
            if (!preg_match(
                '~^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$~i',
                $mimetype
            )) {
                $mimetype = 'application/octet-stream';
            }

            header('Content-Type: ' . $mimetype);
            header('Content-Length: ' . strlen($content));
            header('Cache-Control: private, no-store, max-age=0');
            header('X-Content-Type-Options: nosniff');
            header('Content-Security-Policy: sandbox');
            header(
                "Content-Disposition: attachment; filename*=UTF-8''" .
                rawurlencode($filename)
            );
            echo $content;
            exit;
        } catch (Throwable $error) {
            rcube::raise_error([
                'code' => 600,
                'type' => 'php',
                'message' => 'AI attachment download failed: ' .
                    $error->getMessage(),
            ], true, false);
            $this->attachment_error('Lecture de la pièce jointe impossible.', 500);
        }
    }

    public function download_archive()
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->attachment_error('Session Roundcube absente.', 401);
        }

        if (!class_exists('ZipArchive')) {
            $this->attachment_error('L’extension PHP ZipArchive est absente.', 500);
        }

        $uid = rcube_utils::get_input_string('_uid', rcube_utils::INPUT_POST);
        $mailbox = rcube_utils::get_input_string('_mbox', rcube_utils::INPUT_POST);
        $summary = rcube_utils::get_input_string('_summary', rcube_utils::INPUT_POST);
        $summary = mb_substr(trim($summary), 0, 100000);

        if (!$uid || str_contains($uid, ',')) {
            $this->attachment_error('Courriel invalide.', 400);
        }
        if (!$mailbox) {
            $mailbox = $this->rcmail->storage->get_folder();
        }

        $archive_path = tempnam(sys_get_temp_dir(), 'ai-roundcube-');
        if ($archive_path === false) {
            $this->attachment_error('Création du fichier temporaire impossible.', 500);
        }

        try {
            $message = new rcube_message($uid, $mailbox);
            $this->rcmail->storage->set_folder($mailbox);
            $eml_stream = fopen('php://temp', 'w+');
            if (!$eml_stream || !$this->rcmail->storage->get_raw_body($uid, $eml_stream)) {
                if (is_resource($eml_stream)) {
                    fclose($eml_stream);
                }
                throw new RuntimeException('Lecture du message EML impossible.');
            }
            rewind($eml_stream);
            $raw = stream_get_contents($eml_stream);
            fclose($eml_stream);
            if (!is_string($raw) || $raw === '') {
                throw new RuntimeException('Lecture du message EML impossible.');
            }

            $max_attachment_bytes = (int) $this->rcmail->config->get(
                'ai_max_attachment_bytes',
                26214400
            );
            $max_archive_bytes = (int) $this->rcmail->config->get(
                'ai_max_archive_source_bytes',
                104857600
            );
            $source_bytes = strlen($raw);
            if ($source_bytes > $max_archive_bytes) {
                throw new RuntimeException('Le courriel dépasse la taille maximale de l’archive.');
            }

            $zip = new ZipArchive();
            if ($zip->open($archive_path, ZipArchive::CREATE | ZipArchive::OVERWRITE) !== true) {
                throw new RuntimeException('Ouverture de l’archive impossible.');
            }

            $manifest = [];
            $zip->addFromString('courriel.eml', $raw);
            $manifest[] = hash('sha256', $raw) . '  courriel.eml';

            $metadata =
                "ARCHIVE DE COURRIEL\n\n" .
                'Objet : ' . trim((string) ($message->headers->subject ?? '')) . "\n" .
                'Expéditeur : ' . trim((string) ($message->headers->from ?? '')) . "\n" .
                'Destinataire : ' . trim((string) ($message->headers->to ?? '')) . "\n" .
                'Copie : ' . trim((string) ($message->headers->cc ?? '')) . "\n" .
                'Copie cachée : ' . trim((string) ($message->headers->bcc ?? '')) . "\n" .
                'Date : ' . trim((string) ($message->headers->date ?? '')) . "\n" .
                'Boîte : ' . $mailbox . "\n" .
                'UID : ' . $uid . "\n" .
                'Créée le : ' . gmdate('c') . "\n";
            $zip->addFromString('metadonnees.txt', $metadata);
            $manifest[] = hash('sha256', $metadata) . '  metadonnees.txt';

            if ($summary !== '') {
                $summary_content = "SYNTHÈSE GÉNÉRÉE PAR IA — À VÉRIFIER\n\n" . $summary . "\n";
                $zip->addFromString('synthese.txt', $summary_content);
                $manifest[] = hash('sha256', $summary_content) . '  synthese.txt';
            }

            $used_names = [];
            foreach (($message->attachments ?? []) as $attachment) {
                $part_id = trim((string) ($attachment->mime_id ?? ''));
                if ($part_id === '') {
                    continue;
                }
                $declared_size = max(0, (int) ($attachment->size ?? 0));
                if ($declared_size > $max_attachment_bytes) {
                    throw new RuntimeException('Une pièce jointe dépasse la taille maximale autorisée.');
                }
                $content = $message->get_part_content(
                    $part_id,
                    null,
                    true,
                    $max_attachment_bytes + 1,
                    false
                );
                if (!is_string($content) || strlen($content) > $max_attachment_bytes) {
                    throw new RuntimeException('Lecture d’une pièce jointe impossible ou trop volumineuse.');
                }
                $source_bytes += strlen($content);
                if ($source_bytes > $max_archive_bytes) {
                    throw new RuntimeException('Le contenu total dépasse la taille maximale de l’archive.');
                }

                $name = $this->safe_archive_filename((string) (
                    $attachment->filename
                    ?? $attachment->name
                    ?? ('piece-' . $part_id)
                ));
                $base_name = $name;
                $counter = 2;
                while (isset($used_names[mb_strtolower($name)])) {
                    $extension = pathinfo($base_name, PATHINFO_EXTENSION);
                    $stem = pathinfo($base_name, PATHINFO_FILENAME);
                    $name = $stem . '-' . $counter . ($extension !== '' ? '.' . $extension : '');
                    $counter++;
                }
                $used_names[mb_strtolower($name)] = true;
                $entry = 'pieces_jointes/' . $name;
                $zip->addFromString($entry, $content);
                $manifest[] = hash('sha256', $content) . '  ' . $entry;
            }

            $manifest_content = implode("\n", $manifest) . "\n";
            $zip->addFromString('SHA256SUMS.txt', $manifest_content);
            if (!$zip->close()) {
                throw new RuntimeException('Finalisation de l’archive impossible.');
            }

            $download_name = 'dossier-courriel-' . gmdate('Y-m-d-His') . '.zip';
            header('Content-Type: application/zip');
            header('Content-Length: ' . filesize($archive_path));
            header('Cache-Control: private, no-store, max-age=0');
            header('X-Content-Type-Options: nosniff');
            header(
                "Content-Disposition: attachment; filename*=UTF-8''" .
                rawurlencode($download_name)
            );
            readfile($archive_path);
            @unlink($archive_path);
            exit;
        } catch (Throwable $error) {
            @unlink($archive_path);
            rcube::raise_error([
                'code' => 600,
                'type' => 'php',
                'message' => 'AI archive creation failed: ' . $error->getMessage(),
            ], true, false);
            $this->attachment_error('Création de l’archive impossible.', 500);
        }
    }

    private function invoice_ninja_request(
        string $method,
        string $path,
        ?array $payload = null
    ): array {
        $base_url = rtrim(trim((string) $this->rcmail->config->get(
            'ai_invoice_ninja_url',
            ''
        )), '/');
        if ($base_url === '' || !filter_var($base_url, FILTER_VALIDATE_URL)) {
            throw new RuntimeException('URL Invoice Ninja absente ou invalide.');
        }
        $parts = parse_url($base_url);
        if (!is_array($parts) || strtolower((string) ($parts['scheme'] ?? '')) !== 'https') {
            throw new RuntimeException('Invoice Ninja doit être joint exclusivement en HTTPS.');
        }
        if (isset($parts['user']) || isset($parts['pass']) || isset($parts['query']) || isset($parts['fragment'])) {
            throw new RuntimeException('URL Invoice Ninja non conforme.');
        }

        $token_file = trim((string) $this->rcmail->config->get(
            'ai_invoice_ninja_token_file',
            '/etc/roundcube/ai-invoice-ninja.token'
        ));
        if ($token_file === '' || !is_file($token_file) || !is_readable($token_file)) {
            throw new RuntimeException('Fichier de jeton Invoice Ninja absent ou illisible.');
        }
        $token_perms = fileperms($token_file);
        if ($token_perms === false || (($token_perms & 0007) !== 0) || (($token_perms & 0022) !== 0)) {
            throw new RuntimeException('Permissions du fichier de jeton Invoice Ninja trop larges.');
        }
        $token_size = filesize($token_file);
        if ($token_size === false || $token_size < 16 || $token_size > 4096) {
            throw new RuntimeException('Fichier de jeton Invoice Ninja invalide.');
        }
        $api_token = trim((string) file_get_contents($token_file));
        if ($api_token === '' || preg_match('/[\r\n]/', $api_token)) {
            throw new RuntimeException('Jeton Invoice Ninja invalide.');
        }

        $method = strtoupper($method);
        if (!in_array($method, ['GET', 'POST'], true)) {
            throw new RuntimeException('Méthode Invoice Ninja interdite.');
        }
        if (!preg_match('~^/api/v1/[A-Za-z0-9_/?&=%|.-]+$~', $path)) {
            throw new RuntimeException('Chemin Invoice Ninja invalide.');
        }

        $curl = curl_init($base_url . $path);
        if ($curl === false) {
            throw new RuntimeException('Initialisation cURL impossible.');
        }
        $timeout = max(5, min(90, (int) $this->rcmail->config->get(
            'ai_invoice_ninja_timeout',
            30
        )));
        $headers = [
            'Accept: application/json',
            'Content-Type: application/json',
            'X-Requested-With: XMLHttpRequest',
            'X-API-TOKEN: ' . $api_token,
        ];
        $options = [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 10,
            CURLOPT_TIMEOUT => $timeout,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_MAXREDIRS => 0,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_HTTPHEADER => $headers,
        ];
        if ($method === 'POST') {
            $options[CURLOPT_POSTFIELDS] = json_encode(
                $payload ?? [],
                JSON_UNESCAPED_UNICODE |
                JSON_UNESCAPED_SLASHES |
                JSON_THROW_ON_ERROR
            );
        }
        curl_setopt_array($curl, $options);
        $raw = curl_exec($curl);
        $curl_error = curl_error($curl);
        $http_code = (int) curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);

        if ($raw === false || $curl_error !== '') {
            throw new RuntimeException('Connexion Invoice Ninja impossible: ' . $curl_error);
        }
        if (strlen($raw) > 5242880) {
            throw new RuntimeException('Réponse Invoice Ninja anormalement volumineuse.');
        }

        $decoded = json_decode($raw, true);
        if ($http_code < 200 || $http_code >= 300) {
            throw new RuntimeException('Invoice Ninja HTTP ' . $http_code);
        }
        if (!is_array($decoded)) {
            throw new RuntimeException('Réponse JSON Invoice Ninja invalide.');
        }
        return $decoded;
    }

    public function v8_action()
    {
        $this->require_v8_session();
        try {
            $db = $this->v8_db();
            $op = trim(rcube_utils::get_input_string('_op', rcube_utils::INPUT_POST));
            $user_id = (string) $this->rcmail->user->ID;
            if ($op === 'create') {
                $client_id = trim(rcube_utils::get_input_string('_client_request_id', rcube_utils::INPUT_POST));
                $type = trim(rcube_utils::get_input_string('_action_type', rcube_utils::INPUT_POST));
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $message_key = $this->v8_limited_input('_message_key', 64);
                if (!$this->v8_is_uuid($client_id) || !preg_match('/^[a-z0-9_-]{1,80}$/', $type)) {
                    $this->json_error('Identifiant ou type d’action V8 invalide.', 400);
                }
                if ($message_key !== '' && !preg_match('/^[a-f0-9]{64}$/', $message_key)) {
                    $this->json_error('Empreinte du courriel invalide.', 400);
                }
                $action_id = $this->v8_uuid();
                $db->beginTransaction();
                $stmt = $db->prepare(
                    'INSERT INTO ai_integration_actions '
                    . '(action_id,user_id,client_request_id,action_type,folder_id,message_key,status,progress) '
                    . "VALUES (:id,:user_id,:client_id,:type,NULLIF(:folder_id,''),NULLIF(:message_key,''),'running',:progress) "
                    . 'ON CONFLICT (user_id,client_request_id) DO NOTHING'
                );
                $stmt->execute([
                    ':id' => $action_id,
                    ':user_id' => $user_id,
                    ':client_id' => $client_id,
                    ':type' => $type,
                    ':folder_id' => $folder_id,
                    ':message_key' => $message_key,
                    ':progress' => 'Action enregistrée. Préparation en cours.',
                ]);
                $created = $stmt->rowCount() === 1;
                if (!$created) {
                    $stmt = $db->prepare(
                        'SELECT * FROM ai_integration_actions WHERE user_id=:user_id AND client_request_id=:client_id'
                    );
                    $stmt->execute([':user_id' => $user_id, ':client_id' => $client_id]);
                    $action = $stmt->fetch(PDO::FETCH_ASSOC);
                    $db->commit();
                    $this->json_response(['action' => $this->v8_public_action($action), 'duplicate' => true]);
                }
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => $folder_id,
                    'message_key' => $message_key,
                    'correlation_id' => $action_id,
                    'action' => $type,
                    'phase' => 'started',
                    'status' => 'running',
                ]);
                $db->commit();
                $this->json_response([
                    'action' => $this->v8_public_action([
                        'action_id' => $action_id,
                        'action_type' => $type,
                        'folder_id' => $folder_id,
                        'message_key' => $message_key,
                        'status' => 'running',
                        'progress' => 'Action enregistrée. Préparation en cours.',
                        'started_at' => gmdate('c'),
                        'updated_at' => gmdate('c'),
                    ]),
                    'duplicate' => false,
                ], 201);
            }

            if ($op === 'transition') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $next = $this->v8_limited_input('_status', 32);
                $progress = $this->v8_limited_input('_progress', 500);
                $preview = $this->v8_json_input('_preview', 65536);
                $result = $this->v8_json_input('_result', 65536);
                $preview_hash_input = $this->v8_limited_input('_preview_hash', 64);
                if (!$this->v8_is_uuid($action_id)) {
                    $this->json_error('Action V8 invalide.', 400);
                }
                $db->beginTransaction();
                $stmt = $db->prepare(
                    'SELECT * FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id FOR UPDATE'
                );
                $stmt->execute([':id' => $action_id, ':user_id' => $user_id]);
                $action = $stmt->fetch(PDO::FETCH_ASSOC);
                if (!$action) {
                    $db->rollBack();
                    $this->json_error('Action V8 introuvable.', 404);
                }
                $current = (string) $action['status'];
                $allowed = [
                    'queued' => ['running', 'awaiting_confirmation', 'failed', 'cancelled'],
                    'running' => ['running', 'awaiting_confirmation', 'succeeded', 'partially_succeeded', 'failed', 'cancelled'],
                    'awaiting_confirmation' => ['running', 'cancelled', 'failed'],
                    'succeeded' => [],
                    'partially_succeeded' => [],
                    'failed' => [],
                    'cancelled' => [],
                ];
                if ($next !== $current && !in_array($next, $allowed[$current] ?? [], true)) {
                    $db->rollBack();
                    $this->json_error('Transition d’action interdite.', 409);
                }
                $preview_json = json_encode($preview, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
                $result_json = json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
                $preview_hash = $action['preview_hash'] ?? null;
                $confirmed = false;
                if ($next === 'awaiting_confirmation') {
                    $preview_hash = hash('sha256', $preview_json);
                }
                if ($current === 'awaiting_confirmation' && $next === 'running') {
                    if (!$preview_hash || !hash_equals((string) $preview_hash, $preview_hash_input)) {
                        $db->rollBack();
                        $this->json_error('La prévisualisation confirmée ne correspond plus à l’action.', 409);
                    }
                    $confirmed = true;
                }
                $finished = in_array($next, ['succeeded', 'partially_succeeded', 'failed', 'cancelled'], true);
                $stmt = $db->prepare(
                    'UPDATE ai_integration_actions SET status=:status, progress=:progress, '
                    . "preview=CASE WHEN CAST(:write_preview AS boolean) THEN CAST(:preview AS jsonb) ELSE preview END, "
                    . "result=CASE WHEN CAST(:write_result AS boolean) THEN CAST(:result AS jsonb) ELSE result END, "
                    . 'preview_hash=:preview_hash, '
                    . 'confirmed_at=CASE WHEN CAST(:confirmed AS boolean) THEN NOW() ELSE confirmed_at END, '
                    . 'updated_at=NOW(), finished_at=CASE WHEN CAST(:finished AS boolean) THEN NOW() ELSE finished_at END '
                    . 'WHERE action_id=:id AND user_id=:user_id RETURNING *'
                );
                $stmt->execute([
                    ':status' => $next,
                    ':progress' => $progress !== '' ? $progress : (string) $action['progress'],
                    ':write_preview' => $next === 'awaiting_confirmation' ? 'true' : 'false',
                    ':preview' => $preview_json,
                    ':write_result' => $result !== [] ? 'true' : 'false',
                    ':result' => $result_json,
                    ':preview_hash' => $preview_hash,
                    ':confirmed' => $confirmed ? 'true' : 'false',
                    ':finished' => $finished ? 'true' : 'false',
                    ':id' => $action_id,
                    ':user_id' => $user_id,
                ]);
                $updated = $stmt->fetch(PDO::FETCH_ASSOC);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => (string) ($action['folder_id'] ?? ''),
                    'message_key' => (string) ($action['message_key'] ?? ''),
                    'correlation_id' => $action_id,
                    'action' => (string) $action['action_type'],
                    'phase' => $confirmed ? 'confirmed' : $next,
                    'status' => $next,
                    'details' => $result !== [] ? $result : [],
                    'safe_error_message' => $next === 'failed' ? $progress : '',
                ]);
                $db->commit();
                $this->json_response(['action' => $this->v8_public_action($updated)]);
            }

            if ($op === 'list') {
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $sql = 'SELECT * FROM ai_integration_actions WHERE user_id=:user_id';
                $params = [':user_id' => $user_id];
                if ($folder_id !== '') {
                    $sql .= ' AND folder_id=:folder_id';
                    $params[':folder_id'] = $folder_id;
                }
                $sql .= ' ORDER BY updated_at DESC LIMIT 50';
                $stmt = $db->prepare($sql);
                $stmt->execute($params);
                $actions = array_map([$this, 'v8_public_action'], $stmt->fetchAll(PDO::FETCH_ASSOC));
                $this->json_response(['actions' => $actions]);
            }

            if ($op === 'audit_list') {
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                if ($folder_id === '') $this->json_error('Projet obligatoire.', 400);
                $stmt = $db->prepare(
                    'SELECT audit_id,audit_seq,occurred_at,correlation_id,action,phase,target,object_type,object_id,status,'
                    . 'safe_error_message,event_hash FROM ai_audit_events '
                    . 'WHERE user_id=:user_id AND folder_id=:folder_id ORDER BY audit_seq DESC LIMIT 200'
                );
                $stmt->execute([':user_id' => $user_id, ':folder_id' => $folder_id]);
                $this->json_response(['audit_events' => $stmt->fetchAll(PDO::FETCH_ASSOC)]);
            }

            if ($op === 'activity_list') {
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                if ($folder_id === '') $this->json_error('Projet obligatoire.', 400);
                $stmt = $db->prepare(
                    'SELECT * FROM ai_project_activities WHERE user_id=:user_id AND folder_id=:folder_id '
                    . 'ORDER BY created_at DESC LIMIT 200'
                );
                $stmt->execute([':user_id' => $user_id, ':folder_id' => $folder_id]);
                $this->json_response(['activities' => $stmt->fetchAll(PDO::FETCH_ASSOC)]);
            }

            if ($op === 'activity_create') {
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $type = $this->v8_limited_input('_activity_type', 80);
                $title = $this->v8_limited_input('_title', 300);
                $description = $this->v8_limited_input('_description', 10000);
                $status = $this->v8_limited_input('_activity_status', 24) ?: 'done';
                $correlation_id = $this->v8_limited_input('_action_id', 36);
                $source_id = $this->v8_limited_input('_source_id', 300);
                $source_quote = $this->v8_limited_input('_source_quote', 5000);
                $external = $this->v8_json_input('_external_references', 32768);
                if ($folder_id === '' || $title === '' || !preg_match('/^[a-z0-9_-]{1,80}$/', $type)) {
                    $this->json_error('Activité invalide.', 400);
                }
                if (!in_array($status, ['proposed', 'in_progress', 'done', 'cancelled'], true)) {
                    $this->json_error('État d’activité invalide.', 400);
                }
                if (!$this->v8_is_uuid($correlation_id)) {
                    $this->json_error('Identifiant d’audit invalide.', 400);
                }
                $stmt = $db->prepare(
                    "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running'"
                );
                $stmt->execute([':id' => $correlation_id, ':user_id' => $user_id]);
                if (!$stmt->fetchColumn()) {
                    $this->json_error('Cette activité n’a pas été confirmée.', 409);
                }
                $activity_id = $this->v8_uuid();
                $stmt = $db->prepare(
                    'INSERT INTO ai_project_activities '
                    . '(activity_id,user_id,folder_id,activity_type,title,description,status,completed_at,'
                    . 'source_type,source_id,source_quote,external_references,correlation_id) '
                    . "VALUES (:id,:user_id,:folder_id,:type,:title,:description,:status,"
                    . "CASE WHEN :status='done' THEN NOW() ELSE NULL END,'roundcube',NULLIF(:source_id,''),"
                    . "NULLIF(:source_quote,''),CAST(:external AS jsonb),NULLIF(:correlation_id,''))"
                );
                $stmt->execute([
                    ':id' => $activity_id,
                    ':user_id' => $user_id,
                    ':folder_id' => $folder_id,
                    ':type' => $type,
                    ':title' => $title,
                    ':description' => $description,
                    ':status' => $status,
                    ':source_id' => $source_id,
                    ':source_quote' => $source_quote,
                    ':external' => json_encode($external, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
                    ':correlation_id' => $correlation_id,
                ]);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => $folder_id,
                    'message_key' => '',
                    'correlation_id' => $correlation_id,
                    'action' => 'project_activity_create',
                    'phase' => 'object_created',
                    'target' => 'roundcube_postgresql',
                    'object_type' => 'project_activity',
                    'object_id' => $activity_id,
                    'status' => 'succeeded',
                    'details' => ['activity_type' => $type, 'activity_status' => $status],
                ]);
                $this->json_response(['created' => true, 'activity_id' => $activity_id], 201);
            }

            if ($op === 'activity_update') {
                $activity_id = $this->v8_limited_input('_activity_id', 36);
                $status = $this->v8_limited_input('_activity_status', 24);
                $action_id = $this->v8_limited_input('_action_id', 36);
                if (!$this->v8_is_uuid($activity_id) || !$this->v8_is_uuid($action_id)
                    || !in_array($status, ['proposed', 'in_progress', 'done', 'cancelled'], true)) {
                    $this->json_error('Mise à jour d’activité invalide.', 400);
                }
                $stmt = $db->prepare(
                    "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running'"
                );
                $stmt->execute([':id' => $action_id, ':user_id' => $user_id]);
                if (!$stmt->fetchColumn()) $this->json_error('Cette modification n’a pas été confirmée.', 409);
                $stmt = $db->prepare(
                    'UPDATE ai_project_activities SET status=:status, '
                    . "completed_at=CASE WHEN :status='done' THEN NOW() ELSE NULL END, updated_at=NOW() "
                    . 'WHERE activity_id=:id AND user_id=:user_id RETURNING *'
                );
                $stmt->execute([':status' => $status, ':id' => $activity_id, ':user_id' => $user_id]);
                $updated = $stmt->fetch(PDO::FETCH_ASSOC);
                if (!$updated) $this->json_error('Activité introuvable.', 404);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => (string) ($updated['folder_id'] ?? ''),
                    'message_key' => '',
                    'correlation_id' => $action_id,
                    'action' => 'project_activity_update',
                    'phase' => 'object_updated',
                    'target' => 'roundcube_postgresql',
                    'object_type' => 'project_activity',
                    'object_id' => $activity_id,
                    'status' => 'succeeded',
                    'details' => ['activity_status' => $status],
                ]);
                $this->json_response(['activity' => $updated]);
            }

            if ($op === 'event_find') {
                $fingerprint = $this->v8_limited_input('_fingerprint', 64);
                if (!preg_match('/^[a-f0-9]{64}$/', $fingerprint)) {
                    $this->json_error('Empreinte d’événement invalide.', 400);
                }
                $stmt = $db->prepare(
                    'SELECT * FROM ai_calendar_event_links WHERE user_id=:user_id AND fingerprint=:fingerprint'
                );
                $stmt->execute([':user_id' => $user_id, ':fingerprint' => $fingerprint]);
                $this->json_response(['event' => $stmt->fetch(PDO::FETCH_ASSOC) ?: null]);
            }

            if ($op === 'event_reserve') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $fingerprint = $this->v8_limited_input('_fingerprint', 64);
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $message_key = $this->v8_limited_input('_message_key', 64);
                $title = $this->v8_limited_input('_title', 300);
                $start = $this->v8_iso_date('_start');
                $end = $this->v8_iso_date('_end');
                $override = $this->v8_limited_input('_override_reason', 1000);
                if (!$this->v8_is_uuid($action_id) || !preg_match('/^[a-f0-9]{64}$/', $fingerprint) || $title === '') {
                    $this->json_error('Réservation d’événement invalide.', 400);
                }
                if ($end <= $start) $this->json_error('Période d’événement invalide.', 400);
                $stmt = $db->prepare(
                    "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running'"
                );
                $stmt->execute([':id' => $action_id, ':user_id' => $user_id]);
                if (!$stmt->fetchColumn()) {
                    $this->json_error('Cette écriture n’a pas été confirmée.', 409);
                }
                $logical_id = $this->v8_uuid();
                $stmt = $db->prepare(
                    'INSERT INTO ai_calendar_event_links '
                    . '(logical_event_id,user_id,folder_id,message_key,fingerprint,title,start_at,end_at,status,override_reason,correlation_id) '
                    . "VALUES (:id,:user_id,NULLIF(:folder_id,''),NULLIF(:message_key,''),:fingerprint,:title,:start_at,:end_at,"
                    . "'reserved',NULLIF(:override,''),:correlation_id) ON CONFLICT (user_id,fingerprint) DO NOTHING"
                );
                $stmt->execute([
                    ':id' => $logical_id,
                    ':user_id' => $user_id,
                    ':folder_id' => $folder_id,
                    ':message_key' => $message_key,
                    ':fingerprint' => $fingerprint,
                    ':title' => $title,
                    ':start_at' => $start->format(DateTimeInterface::ATOM),
                    ':end_at' => $end->format(DateTimeInterface::ATOM),
                    ':override' => $override,
                    ':correlation_id' => $action_id,
                ]);
                if ($stmt->rowCount() !== 1) {
                    $stmt = $db->prepare(
                        'SELECT * FROM ai_calendar_event_links WHERE user_id=:user_id AND fingerprint=:fingerprint'
                    );
                    $stmt->execute([':user_id' => $user_id, ':fingerprint' => $fingerprint]);
                    $this->json_response(['reserved' => false, 'duplicate' => $stmt->fetch(PDO::FETCH_ASSOC)], 409);
                }
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => $folder_id,
                    'message_key' => $message_key,
                    'correlation_id' => $action_id,
                    'action' => 'calendar_event_reserve',
                    'phase' => 'fingerprint_reserved',
                    'target' => 'roundcube_postgresql',
                    'object_type' => 'calendar_event_link',
                    'object_id' => $logical_id,
                    'status' => 'running',
                    'details' => ['fingerprint' => $fingerprint],
                ]);
                $this->json_response(['reserved' => true, 'logical_event_id' => $logical_id], 201);
            }

            if ($op === 'event_resume') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $fingerprint = $this->v8_limited_input('_fingerprint', 64);
                if (!$this->v8_is_uuid($action_id) || !preg_match('/^[a-f0-9]{64}$/', $fingerprint)) {
                    $this->json_error('Reprise d’événement invalide.', 400);
                }
                $stmt = $db->prepare(
                    "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running'"
                );
                $stmt->execute([':id' => $action_id, ':user_id' => $user_id]);
                if (!$stmt->fetchColumn()) {
                    $this->json_error('Cette reprise n’a pas été confirmée.', 409);
                }
                $stmt = $db->prepare(
                    "UPDATE ai_calendar_event_links SET status='reserved', correlation_id=:action_id, updated_at=NOW() "
                    . "WHERE user_id=:user_id AND fingerprint=:fingerprint "
                    . "AND status IN ('partially_succeeded','failed') RETURNING *"
                );
                $stmt->execute([
                    ':action_id' => $action_id,
                    ':user_id' => $user_id,
                    ':fingerprint' => $fingerprint,
                ]);
                $event = $stmt->fetch(PDO::FETCH_ASSOC);
                if (!$event) {
                    $this->json_error('Aucun succès partiel récupérable pour cette empreinte.', 409);
                }
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => (string) ($event['folder_id'] ?? ''),
                    'message_key' => (string) ($event['message_key'] ?? ''),
                    'correlation_id' => $action_id,
                    'action' => 'calendar_event_recover',
                    'phase' => 'resume_reserved',
                    'target' => 'openwebui_nextcloud',
                    'object_type' => 'calendar_event_link',
                    'object_id' => (string) $event['logical_event_id'],
                    'status' => 'running',
                    'details' => ['fingerprint' => $fingerprint],
                ]);
                $this->json_response(['resumed' => true, 'event' => $event]);
            }

            if ($op === 'event_update') {
                $logical_id = $this->v8_limited_input('_logical_event_id', 36);
                $action_id = $this->v8_limited_input('_action_id', 36);
                $status = $this->v8_limited_input('_event_status', 32);
                $owu_calendar = $this->v8_limited_input('_openwebui_calendar_id', 200);
                $owu_event = $this->v8_limited_input('_openwebui_event_id', 200);
                $nc_calendar = $this->v8_limited_input('_nextcloud_calendar_name', 300);
                $nc_uid = $this->v8_limited_input('_nextcloud_uid', 300);
                if (!$this->v8_is_uuid($logical_id) || !$this->v8_is_uuid($action_id)) {
                    $this->json_error('Lien d’événement invalide.', 400);
                }
                if (!in_array($status, ['succeeded', 'partially_succeeded', 'failed'], true)) {
                    $this->json_error('État d’événement invalide.', 400);
                }
                $stmt = $db->prepare(
                    'UPDATE ai_calendar_event_links SET status=:status, '
                    . 'openwebui_calendar_id=COALESCE(NULLIF(:owu_calendar,\'\'),openwebui_calendar_id), '
                    . 'openwebui_event_id=COALESCE(NULLIF(:owu_event,\'\'),openwebui_event_id), '
                    . 'nextcloud_calendar_name=COALESCE(NULLIF(:nc_calendar,\'\'),nextcloud_calendar_name), '
                    . 'nextcloud_uid=COALESCE(NULLIF(:nc_uid,\'\'),nextcloud_uid), updated_at=NOW() '
                    . 'WHERE logical_event_id=:id AND user_id=:user_id AND correlation_id=:correlation_id RETURNING *'
                );
                $stmt->execute([
                    ':status' => $status,
                    ':owu_calendar' => $owu_calendar,
                    ':owu_event' => $owu_event,
                    ':nc_calendar' => $nc_calendar,
                    ':nc_uid' => $nc_uid,
                    ':id' => $logical_id,
                    ':user_id' => $user_id,
                    ':correlation_id' => $action_id,
                ]);
                $event = $stmt->fetch(PDO::FETCH_ASSOC);
                if (!$event) $this->json_error('Lien d’événement introuvable.', 404);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => (string) ($event['folder_id'] ?? ''),
                    'message_key' => (string) ($event['message_key'] ?? ''),
                    'correlation_id' => $action_id,
                    'action' => 'calendar_event_link_update',
                    'phase' => 'target_results_recorded',
                    'target' => 'openwebui_nextcloud',
                    'object_type' => 'calendar_event_link',
                    'object_id' => $logical_id,
                    'status' => $status,
                    'details' => [
                        'openwebui_event_id' => $owu_event ?: null,
                        'nextcloud_uid' => $nc_uid ?: null,
                    ],
                ]);
                $this->json_response(['updated' => true]);
            }

            if ($op === 'file_import_find') {
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $source_fingerprint = $this->v8_limited_input('_source_fingerprint', 64);
                $source_etag = $this->v8_limited_input('_source_etag', 300);
                $content_sha256 = $this->v8_limited_input('_content_sha256', 64);
                if ($folder_id === '' || !preg_match('/^[a-f0-9]{64}$/', $source_fingerprint)
                    || ($content_sha256 !== '' && !preg_match('/^[a-f0-9]{64}$/', $content_sha256))) {
                    $this->json_error('Empreinte d’import invalide.', 400);
                }
                $stmt = $db->prepare(
                    'SELECT import_id,source_type,source_etag,content_sha256,openwebui_file_id,status,correlation_id,updated_at '
                    . 'FROM ai_file_import_fingerprints WHERE user_id=:user_id AND folder_id=:folder_id '
                    . "AND status='succeeded' AND ((source_fingerprint=:source_fingerprint AND source_etag=:source_etag) "
                    . "OR (NULLIF(:content_sha256,'') IS NOT NULL AND content_sha256=NULLIF(:content_sha256,''))) "
                    . 'ORDER BY updated_at DESC LIMIT 1'
                );
                $stmt->execute([
                    ':user_id' => $user_id,
                    ':folder_id' => $folder_id,
                    ':source_fingerprint' => $source_fingerprint,
                    ':source_etag' => $source_etag,
                    ':content_sha256' => $content_sha256,
                ]);
                $this->json_response(['import' => $stmt->fetch(PDO::FETCH_ASSOC) ?: null]);
            }

            if ($op === 'file_import_reserve') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $source_fingerprint = $this->v8_limited_input('_source_fingerprint', 64);
                $source_etag = $this->v8_limited_input('_source_etag', 300);
                $content_sha256 = $this->v8_limited_input('_content_sha256', 64);
                $node_id = trim(rcube_utils::get_input_string('_node_id', rcube_utils::INPUT_POST));
                if (!$this->v8_is_uuid($action_id) || $folder_id === ''
                    || !preg_match('/^[a-f0-9]{64}$/', $source_fingerprint)
                    || !preg_match('/^[a-f0-9]{64}$/', $content_sha256)
                    || $node_id === '' || strlen($node_id) > 8192) {
                    $this->json_error('Réservation d’import invalide.', 400);
                }
                $node = $this->v8_verify_node($node_id);
                if (($node['type'] ?? '') !== 'file') {
                    $this->json_error('Le nœud Nextcloud n’est pas un fichier.', 400);
                }
                $expected_source = hash_hmac('sha256', (string) $node['path'], $this->v8_node_secret());
                if (!hash_equals($expected_source, $source_fingerprint)
                    || !hash_equals((string) ($node['etag'] ?? ''), $source_etag)) {
                    $this->json_error('Les empreintes ne correspondent pas au nœud Nextcloud signé.', 409);
                }
                $db->beginTransaction();
                $stmt = $db->prepare(
                    "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running' FOR UPDATE"
                );
                $stmt->execute([':id' => $action_id, ':user_id' => $user_id]);
                if (!$stmt->fetchColumn()) {
                    $db->rollBack();
                    $this->json_error('Cet import n’a pas été confirmé.', 409);
                }
                $lock = $db->prepare("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))");
                $lock->execute([':lock_key' => $user_id . '|' . $folder_id . '|' . $source_fingerprint]);
                $stmt = $db->prepare(
                    'SELECT * FROM ai_file_import_fingerprints WHERE user_id=:user_id AND folder_id=:folder_id '
                    . "AND status='succeeded' AND ((source_fingerprint=:source_fingerprint AND source_etag=:source_etag) "
                    . 'OR content_sha256=:content_sha256) ORDER BY updated_at DESC LIMIT 1'
                );
                $stmt->execute([
                    ':user_id' => $user_id,
                    ':folder_id' => $folder_id,
                    ':source_fingerprint' => $source_fingerprint,
                    ':source_etag' => $source_etag,
                    ':content_sha256' => $content_sha256,
                ]);
                $duplicate = $stmt->fetch(PDO::FETCH_ASSOC);
                if ($duplicate) {
                    $db->commit();
                    $this->json_response(['reserved' => false, 'duplicate' => $duplicate]);
                }
                $stmt = $db->prepare(
                    'SELECT * FROM ai_file_import_fingerprints WHERE user_id=:user_id AND folder_id=:folder_id '
                    . 'AND source_fingerprint=:source_fingerprint AND source_etag=:source_etag FOR UPDATE'
                );
                $stmt->execute([
                    ':user_id' => $user_id,
                    ':folder_id' => $folder_id,
                    ':source_fingerprint' => $source_fingerprint,
                    ':source_etag' => $source_etag,
                ]);
                $existing = $stmt->fetch(PDO::FETCH_ASSOC);
                if ($existing && $existing['status'] === 'reserved'
                    && $existing['correlation_id'] !== $action_id
                    && strtotime((string) $existing['updated_at']) > time() - 1200) {
                    $db->rollBack();
                    $this->json_error('Ce fichier est déjà en cours d’import dans ce projet.', 409);
                }
                $import_id = $existing ? (string) $existing['import_id'] : $this->v8_uuid();
                if ($existing) {
                    $stmt = $db->prepare(
                        "UPDATE ai_file_import_fingerprints SET status='reserved',content_sha256=:content_sha256,"
                        . 'openwebui_file_id=NULL,correlation_id=:correlation_id,updated_at=NOW() WHERE import_id=:id'
                    );
                } else {
                    $stmt = $db->prepare(
                        'INSERT INTO ai_file_import_fingerprints '
                        . '(import_id,user_id,folder_id,source_type,source_fingerprint,source_etag,content_sha256,status,correlation_id) '
                        . "VALUES (:id,:user_id,:folder_id,'nextcloud',:source_fingerprint,:source_etag,:content_sha256,'reserved',:correlation_id)"
                    );
                }
                $parameters = [
                    ':id' => $import_id,
                    ':content_sha256' => $content_sha256,
                    ':correlation_id' => $action_id,
                ];
                if (!$existing) {
                    $parameters[':user_id'] = $user_id;
                    $parameters[':folder_id'] = $folder_id;
                    $parameters[':source_fingerprint'] = $source_fingerprint;
                    $parameters[':source_etag'] = $source_etag;
                }
                $stmt->execute($parameters);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => $folder_id,
                    'message_key' => '',
                    'correlation_id' => $action_id,
                    'action' => 'nextcloud_file_import',
                    'phase' => $existing ? 'reservation_recovered' : 'fingerprint_reserved',
                    'target' => 'openwebui_knowledge',
                    'object_type' => 'file_import',
                    'object_id' => $import_id,
                    'status' => 'running',
                    'details' => ['source_fingerprint' => $source_fingerprint, 'content_sha256' => $content_sha256],
                ]);
                $db->commit();
                $this->json_response(['reserved' => true, 'import_id' => $import_id, 'recovered' => (bool) $existing], 201);
            }

            if ($op === 'file_import_complete') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $import_id = $this->v8_limited_input('_import_id', 36);
                $file_id = $this->v8_limited_input('_openwebui_file_id', 200);
                $content_sha256 = $this->v8_limited_input('_content_sha256', 64);
                if (!$this->v8_is_uuid($action_id) || !$this->v8_is_uuid($import_id) || $file_id === ''
                    || !preg_match('/^[a-f0-9]{64}$/', $content_sha256)) {
                    $this->json_error('Finalisation d’import invalide.', 400);
                }
                $db->beginTransaction();
                $lock = $db->prepare("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))");
                $lock->execute([':lock_key' => $user_id . '|content|' . $content_sha256]);
                $stmt = $db->prepare(
                    "SELECT * FROM ai_file_import_fingerprints WHERE import_id=:id AND user_id=:user_id "
                    . "AND correlation_id=:correlation_id AND status='reserved' FOR UPDATE"
                );
                $stmt->execute([':id' => $import_id, ':user_id' => $user_id, ':correlation_id' => $action_id]);
                $record = $stmt->fetch(PDO::FETCH_ASSOC);
                if (!$record) {
                    $db->rollBack();
                    $this->json_error('Réservation d’import introuvable ou expirée.', 409);
                }
                $stmt = $db->prepare(
                    "SELECT * FROM ai_file_import_fingerprints WHERE user_id=:user_id AND folder_id=:folder_id "
                    . "AND content_sha256=:content_sha256 AND status='succeeded' AND import_id<>:id LIMIT 1"
                );
                $stmt->execute([
                    ':user_id' => $user_id,
                    ':folder_id' => (string) $record['folder_id'],
                    ':content_sha256' => $content_sha256,
                    ':id' => $import_id,
                ]);
                $duplicate = $stmt->fetch(PDO::FETCH_ASSOC);
                if ($duplicate) {
                    $stmt = $db->prepare("UPDATE ai_file_import_fingerprints SET status='failed',updated_at=NOW() WHERE import_id=:id");
                    $stmt->execute([':id' => $import_id]);
                    $this->v8_audit($db, [
                        'user_id' => $user_id,
                        'folder_id' => (string) $record['folder_id'],
                        'message_key' => '',
                        'correlation_id' => $action_id,
                        'action' => 'nextcloud_file_import',
                        'phase' => 'content_duplicate_detected',
                        'target' => 'openwebui_knowledge',
                        'object_type' => 'file_import',
                        'object_id' => $import_id,
                        'status' => 'cancelled',
                        'details' => ['duplicate_file_id' => $duplicate['openwebui_file_id'] ?? null],
                    ]);
                    $db->commit();
                    $this->json_response(['completed' => false, 'duplicate' => $duplicate]);
                }
                $stmt = $db->prepare(
                    "UPDATE ai_file_import_fingerprints SET content_sha256=:content_sha256,openwebui_file_id=:file_id,"
                    . "status='succeeded',updated_at=NOW() WHERE import_id=:id RETURNING *"
                );
                $stmt->execute([':content_sha256' => $content_sha256, ':file_id' => $file_id, ':id' => $import_id]);
                $completed = $stmt->fetch(PDO::FETCH_ASSOC);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => (string) $record['folder_id'],
                    'message_key' => '',
                    'correlation_id' => $action_id,
                    'action' => 'nextcloud_file_import',
                    'phase' => 'indexed',
                    'target' => 'openwebui_knowledge',
                    'object_type' => 'file_import',
                    'object_id' => $import_id,
                    'status' => 'succeeded',
                    'details' => ['openwebui_file_id' => $file_id, 'content_sha256' => $content_sha256],
                ]);
                $db->commit();
                $this->json_response(['completed' => true, 'import' => $completed]);
            }

            if ($op === 'file_import_fail') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $import_id = $this->v8_limited_input('_import_id', 36);
                $safe_error = $this->v8_limited_input('_safe_error', 500);
                if (!$this->v8_is_uuid($action_id) || !$this->v8_is_uuid($import_id)) {
                    $this->json_error('Échec d’import invalide.', 400);
                }
                $stmt = $db->prepare(
                    "UPDATE ai_file_import_fingerprints SET status='failed',updated_at=NOW() "
                    . "WHERE import_id=:id AND user_id=:user_id AND correlation_id=:correlation_id AND status='reserved' RETURNING *"
                );
                $stmt->execute([':id' => $import_id, ':user_id' => $user_id, ':correlation_id' => $action_id]);
                $failed = $stmt->fetch(PDO::FETCH_ASSOC);
                if ($failed) {
                    $this->v8_audit($db, [
                        'user_id' => $user_id,
                        'folder_id' => (string) $failed['folder_id'],
                        'message_key' => '',
                        'correlation_id' => $action_id,
                        'action' => 'nextcloud_file_import',
                        'phase' => 'failed',
                        'target' => 'openwebui_knowledge',
                        'object_type' => 'file_import',
                        'object_id' => $import_id,
                        'status' => 'failed',
                        'safe_error_message' => $safe_error,
                    ]);
                }
                $this->json_response(['updated' => (bool) $failed]);
            }

            if ($op === 'snapshot_latest') {
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                if ($folder_id === '') $this->json_error('Projet obligatoire.', 400);
                $stmt = $db->prepare(
                    'SELECT * FROM ai_project_snapshots WHERE user_id=:user_id AND folder_id=:folder_id '
                    . 'ORDER BY created_at DESC LIMIT 1'
                );
                $stmt->execute([':user_id' => $user_id, ':folder_id' => $folder_id]);
                $this->json_response(['snapshot' => $stmt->fetch(PDO::FETCH_ASSOC) ?: null]);
            }

            if ($op === 'snapshot_create') {
                $action_id = $this->v8_limited_input('_action_id', 36);
                $folder_id = $this->v8_limited_input('_folder_id', 200);
                $chat_id = $this->v8_limited_input('_overview_chat_id', 200);
                $manifest = $this->v8_json_input('_manifest', 262144);
                $versions = $this->v8_json_input('_source_versions', 262144);
                if (!$this->v8_is_uuid($action_id) || $folder_id === '' || $chat_id === '') {
                    $this->json_error('Instantané de dossier invalide.', 400);
                }
                $stmt = $db->prepare(
                    "SELECT confirmed_at FROM ai_integration_actions WHERE action_id=:id AND user_id=:user_id AND status='running'"
                );
                $stmt->execute([':id' => $action_id, ':user_id' => $user_id]);
                if (!$stmt->fetchColumn()) {
                    $this->json_error('Cet instantané n’a pas été confirmé.', 409);
                }
                $manifest_json = json_encode($manifest, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
                $snapshot_id = $this->v8_uuid();
                $stmt = $db->prepare(
                    'INSERT INTO ai_project_snapshots '
                    . '(snapshot_id,user_id,folder_id,overview_chat_id,manifest_hash,manifest,source_versions) '
                    . 'VALUES (:id,:user_id,:folder_id,:chat_id,:hash,CAST(:manifest AS jsonb),CAST(:versions AS jsonb))'
                );
                $stmt->execute([
                    ':id' => $snapshot_id,
                    ':user_id' => $user_id,
                    ':folder_id' => $folder_id,
                    ':chat_id' => $chat_id,
                    ':hash' => hash('sha256', $manifest_json),
                    ':manifest' => $manifest_json,
                    ':versions' => json_encode($versions, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
                ]);
                $this->v8_audit($db, [
                    'user_id' => $user_id,
                    'folder_id' => $folder_id,
                    'message_key' => '',
                    'correlation_id' => $action_id,
                    'action' => 'project_snapshot_create',
                    'phase' => 'object_created',
                    'target' => 'roundcube_postgresql',
                    'object_type' => 'project_snapshot',
                    'object_id' => $snapshot_id,
                    'status' => 'succeeded',
                    'details' => ['overview_chat_id' => $chat_id, 'manifest_hash' => hash('sha256', $manifest_json)],
                ]);
                $this->json_response(['created' => true, 'snapshot_id' => $snapshot_id], 201);
            }

            $this->json_error('Opération V8 inconnue.', 400);
        } catch (PDOException $error) {
            $this->log_integration_error('V8 database operation failed', $error);
            $this->json_error('Journal PostgreSQL V8 indisponible.', 503);
        } catch (Throwable $error) {
            $this->log_integration_error('V8 action failed', $error);
            $this->json_error('Opération V8 impossible. Consultez le journal Roundcube.', 500);
        }
    }

    public function v8_health()
    {
        $this->require_v8_session();
        $checks = [];
        $started = microtime(true);
        try {
            $db = $this->v8_db();
            $db->query('SELECT 1 FROM ai_integration_actions LIMIT 1');
            $checks[] = $this->v8_health_item('PostgreSQL V8', 'ok', $started, 'Schéma accessible en lecture.');
        } catch (Throwable $error) {
            $checks[] = $this->v8_health_item('PostgreSQL V8', 'down', $started, 'Schéma inaccessible ou non migré.');
        }

        $started = microtime(true);
        try {
            $this->invoice_ninja_request('GET', '/api/v1/projects?per_page=1');
            $checks[] = $this->v8_health_item('Invoice Ninja', 'ok', $started, 'Authentification et lecture des projets confirmées.');
        } catch (Throwable $error) {
            $checks[] = $this->v8_health_item('Invoice Ninja', 'down', $started, 'Lecture des projets impossible.');
        }

        $started = microtime(true);
        try {
            $config = $this->v8_nextcloud_config();
            $roots = $this->v8_nextcloud_allowed_roots();
            if (!$roots) throw new RuntimeException('Aucune racine autorisée.');
            foreach ($roots as $root) {
                $this->v8_webdav_list($config, $root, 0);
            }
            $checks[] = $this->v8_health_item(
                'Nextcloud WebDAV',
                'ok',
                $started,
                count($roots) . ' racine(s) autorisée(s) accessible(s).'
            );
        } catch (Throwable $error) {
            $checks[] = $this->v8_health_item('Nextcloud WebDAV', 'down', $started, 'Racine autorisée inaccessible.');
        }

        $this->json_response(['checked_at' => gmdate('c'), 'checks' => $checks]);
    }

    public function v8_nextcloud_browse()
    {
        $this->require_v8_session();
        try {
            $node = trim(rcube_utils::get_input_string('_node_id', rcube_utils::INPUT_POST));
            $roots = $this->v8_nextcloud_allowed_roots();
            if (!$roots) throw new RuntimeException('Aucune racine Nextcloud autorisée.');
            if ($node === '') {
                $items = array_map(function (string $root): array {
                    return [
                        'name' => basename($root),
                        'type' => 'directory',
                        'size' => 0,
                        'etag' => '',
                        'modified' => '',
                        'node_id' => $this->v8_sign_node($root, 'directory'),
                    ];
                }, $roots);
                $this->json_response(['path_label' => 'Racines autorisées', 'parent_node_id' => null, 'items' => $items]);
            }
            $decoded = $this->v8_verify_node($node);
            if (($decoded['type'] ?? '') !== 'directory') {
                $this->json_error('Ce nœud Nextcloud n’est pas un répertoire.', 400);
            }
            $path = (string) $decoded['path'];
            $config = $this->v8_nextcloud_config();
            $items = $this->v8_webdav_list($config, $path, 1);
            $parent = $this->v8_allowed_parent($path);
            $this->json_response([
                'path_label' => $path,
                'parent_node_id' => $parent !== null ? $this->v8_sign_node($parent, 'directory') : null,
                'items' => $items,
            ]);
        } catch (Throwable $error) {
            $this->log_integration_error('V8 Nextcloud browser failed', $error);
            $this->json_error('Navigation Nextcloud refusée ou indisponible.', 502);
        }
    }

    public function v8_nextcloud_events()
    {
        $this->require_v8_session();
        try {
            $start = $this->v8_iso_date('_start');
            $end = $this->v8_iso_date('_end');
            $calendar = $this->v8_limited_input('_calendar', 300);
            $allowed_calendar = trim((string) $this->rcmail->config->get(
                'ai_nextcloud_calendar_name',
                'CABINET EXEMPLE'
            ));
            if ($calendar === '' || !hash_equals($allowed_calendar, $calendar)) {
                $this->json_error('Agenda Nextcloud non autorisé.', 403);
            }
            if ($end <= $start || ($end->getTimestamp() - $start->getTimestamp()) > 2678400) {
                $this->json_error('Période de recherche calendrier invalide.', 400);
            }
            $events = $this->v8_caldav_events($this->v8_nextcloud_config(), $calendar, $start, $end);
            $this->json_response(['events' => $events]);
        } catch (Throwable $error) {
            $this->log_integration_error('V8 Nextcloud calendar search failed', $error);
            $this->json_error('Recherche des événements Nextcloud impossible.', 502);
        }
    }

    private function require_v8_session(): void
    {
        if (!$this->rcmail->user || !$this->rcmail->user->ID) {
            $this->json_error('Session Roundcube absente.', 401);
        }
    }

    private function v8_db(): PDO
    {
        $dsn = trim((string) $this->rcmail->config->get('ai_v8_pg_dsn', ''));
        $username = trim((string) $this->rcmail->config->get('ai_v8_pg_user', ''));
        $password_file = trim((string) $this->rcmail->config->get(
            'ai_v8_pg_password_file',
            '/etc/roundcube/ai-v8-postgresql.token'
        ));
        if (!str_starts_with($dsn, 'pgsql:') || $username === '') {
            throw new RuntimeException('Configuration PostgreSQL V8 absente.');
        }
        $password = $this->v8_read_secret($password_file, 8);
        return new PDO($dsn, $username, $password, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
            PDO::ATTR_TIMEOUT => 5,
        ]);
    }

    private function v8_read_secret(string $path, int $minimum): string
    {
        if ($path === '' || !is_file($path) || !is_readable($path)) {
            throw new RuntimeException('Fichier secret absent ou illisible.');
        }
        $perms = fileperms($path);
        if ($perms === false || (($perms & 0007) !== 0) || (($perms & 0022) !== 0)) {
            throw new RuntimeException('Permissions du fichier secret trop larges.');
        }
        $size = filesize($path);
        if ($size === false || $size < $minimum || $size > 4096) {
            throw new RuntimeException('Taille du fichier secret invalide.');
        }
        $secret = trim((string) file_get_contents($path));
        if ($secret === '' || preg_match('/[\r\n]/', $secret)) {
            throw new RuntimeException('Fichier secret invalide.');
        }
        return $secret;
    }

    private function v8_limited_input(string $name, int $limit): string
    {
        return mb_substr(trim(rcube_utils::get_input_string($name, rcube_utils::INPUT_POST)), 0, $limit);
    }

    private function v8_json_input(string $name, int $limit): array
    {
        $raw = rcube_utils::get_input_string($name, rcube_utils::INPUT_POST);
        if ($raw === '' || $raw === null) return [];
        if (strlen($raw) > $limit) throw new RuntimeException('Données JSON trop volumineuses.');
        $decoded = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        if (!is_array($decoded)) throw new RuntimeException('Objet JSON attendu.');
        return $decoded;
    }

    private function v8_iso_date(string $name): DateTimeImmutable
    {
        $value = $this->v8_limited_input($name, 80);
        try {
            $date = new DateTimeImmutable($value);
        } catch (Throwable $error) {
            throw new RuntimeException('Date ISO invalide.');
        }
        return $date;
    }

    private function v8_is_uuid(string $value): bool
    {
        return (bool) preg_match(
            '/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i',
            $value
        );
    }

    private function v8_uuid(): string
    {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        $hex = bin2hex($bytes);
        return substr($hex, 0, 8) . '-' . substr($hex, 8, 4) . '-' . substr($hex, 12, 4)
            . '-' . substr($hex, 16, 4) . '-' . substr($hex, 20);
    }

    private function v8_public_action($action): ?array
    {
        if (!is_array($action)) return null;
        $preview = $action['preview'] ?? [];
        $result = $action['result'] ?? [];
        if (is_string($preview)) $preview = json_decode($preview, true) ?: [];
        if (is_string($result)) $result = json_decode($result, true) ?: [];
        return [
            'action_id' => (string) ($action['action_id'] ?? ''),
            'action_type' => (string) ($action['action_type'] ?? ''),
            'folder_id' => (string) ($action['folder_id'] ?? ''),
            'message_key' => (string) ($action['message_key'] ?? ''),
            'status' => (string) ($action['status'] ?? ''),
            'progress' => (string) ($action['progress'] ?? ''),
            'preview' => is_array($preview) ? $preview : [],
            'result' => is_array($result) ? $result : [],
            'preview_hash' => (string) ($action['preview_hash'] ?? ''),
            'confirmed_at' => $action['confirmed_at'] ?? null,
            'started_at' => $action['started_at'] ?? null,
            'updated_at' => $action['updated_at'] ?? null,
            'finished_at' => $action['finished_at'] ?? null,
        ];
    }

    private function v8_audit(PDO $db, array $event): void
    {
        $owns_transaction = !$db->inTransaction();
        if ($owns_transaction) $db->beginTransaction();
        try {
            $user_id = (string) ($event['user_id'] ?? '');
            $db->prepare('SELECT pg_advisory_xact_lock(hashtext(:scope))')
                ->execute([':scope' => 'ai-v8-audit:' . $user_id]);
            $stmt = $db->prepare(
            'SELECT event_hash FROM ai_audit_events WHERE user_id=:user_id ORDER BY audit_seq DESC LIMIT 1'
            );
            $stmt->execute([':user_id' => $user_id]);
            $previous = (string) ($stmt->fetchColumn() ?: '');
            $details = is_array($event['details'] ?? null) ? $event['details'] : [];
            $canonical = [
            'user_id' => $user_id,
            'folder_id' => (string) ($event['folder_id'] ?? ''),
            'message_key' => (string) ($event['message_key'] ?? ''),
            'correlation_id' => (string) ($event['correlation_id'] ?? ''),
            'action' => (string) ($event['action'] ?? ''),
            'phase' => (string) ($event['phase'] ?? ''),
            'target' => (string) ($event['target'] ?? 'roundcube'),
            'status' => (string) ($event['status'] ?? ''),
            'details' => $details,
            'previous_hash' => $previous,
            ];
            $payload = json_encode($canonical, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
            $hash = hash('sha256', $previous . "\n" . $payload);
            $stmt = $db->prepare(
            'INSERT INTO ai_audit_events '
            . '(audit_id,user_id,folder_id,message_key,correlation_id,action,phase,target,object_type,object_id,status,'
            . 'payload_hash,details,error_code,safe_error_message,previous_hash,event_hash) '
            . "VALUES (:id,:user_id,NULLIF(:folder_id,''),NULLIF(:message_key,''),:correlation_id,:action,:phase,:target,"
            . "NULLIF(:object_type,''),NULLIF(:object_id,''),:status,:payload_hash,CAST(:details AS jsonb),"
            . "NULLIF(:error_code,''),NULLIF(:safe_error,''),NULLIF(:previous_hash,''),:event_hash)"
            );
            $stmt->execute([
            ':id' => $this->v8_uuid(),
            ':user_id' => $user_id,
            ':folder_id' => $canonical['folder_id'],
            ':message_key' => $canonical['message_key'],
            ':correlation_id' => $canonical['correlation_id'],
            ':action' => $canonical['action'],
            ':phase' => $canonical['phase'],
            ':target' => $canonical['target'],
            ':object_type' => (string) ($event['object_type'] ?? ''),
            ':object_id' => (string) ($event['object_id'] ?? ''),
            ':status' => $canonical['status'],
            ':payload_hash' => hash('sha256', json_encode($details, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)),
            ':details' => json_encode($details, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            ':error_code' => (string) ($event['error_code'] ?? ''),
            ':safe_error' => mb_substr((string) ($event['safe_error_message'] ?? ''), 0, 1000),
            ':previous_hash' => $previous,
            ':event_hash' => $hash,
            ]);
            if ($owns_transaction) $db->commit();
        } catch (Throwable $error) {
            if ($owns_transaction && $db->inTransaction()) $db->rollBack();
            throw $error;
        }
    }

    private function v8_health_item(string $name, string $status, float $started, string $message): array
    {
        return [
            'name' => $name,
            'status' => $status,
            'latency_ms' => (int) round((microtime(true) - $started) * 1000),
            'message' => $message,
        ];
    }

    private function v8_nextcloud_config(): array
    {
        $base = rtrim(trim((string) $this->rcmail->config->get('ai_nextcloud_url', '')), '/');
        $parts = parse_url($base);
        if (!is_array($parts) || strtolower((string) ($parts['scheme'] ?? '')) !== 'https'
            || empty($parts['host']) || isset($parts['user']) || isset($parts['pass'])
            || isset($parts['query']) || isset($parts['fragment'])) {
            throw new RuntimeException('URL Nextcloud HTTPS invalide.');
        }
        $username = trim((string) $this->rcmail->config->get('ai_nextcloud_username', ''));
        if ($username === '' || preg_match('/[\r\n]/', $username)) {
            throw new RuntimeException('Compte Nextcloud invalide.');
        }
        $password_file = trim((string) $this->rcmail->config->get(
            'ai_nextcloud_password_file',
            '/etc/roundcube/ai-nextcloud.token'
        ));
        return [
            'base' => $base,
            'username' => $username,
            'password' => $this->v8_read_secret($password_file, 8),
            'timeout' => max(5, min(60, (int) $this->rcmail->config->get('ai_nextcloud_timeout', 30))),
        ];
    }

    private function v8_nextcloud_allowed_roots(): array
    {
        $configured = $this->rcmail->config->get('ai_nextcloud_allowed_roots', []);
        if (!is_array($configured)) return [];
        $roots = [];
        foreach (array_slice($configured, 0, 20) as $root) {
            $path = $this->v8_normalize_path((string) $root);
            if ($path !== '' && !in_array($path, $roots, true)) $roots[] = $path;
        }
        return $roots;
    }

    private function v8_normalize_path(string $path): string
    {
        $path = str_replace('\\', '/', trim(rawurldecode($path)));
        $path = trim($path, '/');
        if ($path === '' || mb_strlen($path) > 1000 || str_contains($path, "\0")) return '';
        $segments = explode('/', $path);
        foreach ($segments as $segment) {
            if ($segment === '' || $segment === '.' || $segment === '..') return '';
        }
        return implode('/', $segments);
    }

    private function v8_assert_allowed_path(string $path): string
    {
        $path = $this->v8_normalize_path($path);
        if ($path === '') throw new RuntimeException('Chemin Nextcloud invalide.');
        foreach ($this->v8_nextcloud_allowed_roots() as $root) {
            if ($path === $root || str_starts_with($path, $root . '/')) return $path;
        }
        throw new RuntimeException('Chemin Nextcloud hors des racines autorisées.');
    }

    private function v8_assert_allowed_file_path(string $path): string
    {
        $path = $this->v8_assert_allowed_path($path);
        $deny = $this->rcmail->config->get('ai_nextcloud_denied_names', []);
        $deny = is_array($deny) ? array_map('mb_strtolower', $deny) : [];
        foreach (explode('/', $path) as $segment) {
            if (in_array(mb_strtolower($segment), $deny, true)) {
                throw new RuntimeException('Nom Nextcloud interdit.');
            }
        }
        $extensions = $this->rcmail->config->get('ai_nextcloud_allowed_extensions', []);
        $extensions = is_array($extensions) ? array_map('mb_strtolower', $extensions) : [];
        $extension = mb_strtolower(pathinfo($path, PATHINFO_EXTENSION));
        if (!$extensions || !in_array($extension, $extensions, true)) {
            throw new RuntimeException('Extension Nextcloud interdite.');
        }
        return $path;
    }

    private function v8_allowed_parent(string $path): ?string
    {
        foreach ($this->v8_nextcloud_allowed_roots() as $root) {
            if ($path === $root) return null;
            if (str_starts_with($path, $root . '/')) {
                $parent = dirname($path);
                return $parent === '.' ? null : $parent;
            }
        }
        return null;
    }

    private function v8_node_secret(): string
    {
        return $this->v8_read_secret(trim((string) $this->rcmail->config->get(
            'ai_v8_signing_key_file',
            '/etc/roundcube/ai-v8-signing.key'
        )), 32);
    }

    private function v8_b64url_encode(string $value): string
    {
        return rtrim(strtr(base64_encode($value), '+/', '-_'), '=');
    }

    private function v8_b64url_decode(string $value): string
    {
        $padding = strlen($value) % 4;
        if ($padding) $value .= str_repeat('=', 4 - $padding);
        $decoded = base64_decode(strtr($value, '-_', '+/'), true);
        if ($decoded === false) throw new RuntimeException('Nœud Nextcloud invalide.');
        return $decoded;
    }

    private function v8_sign_node(string $path, string $type, string $etag = ''): string
    {
        $path = $this->v8_assert_allowed_path($path);
        $payload = json_encode([
            'path' => $path,
            'type' => $type,
            'etag' => mb_substr($etag, 0, 300),
            'exp' => time() + 900,
            'user_id' => (string) $this->rcmail->user->ID,
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $key = hash('sha256', $this->v8_node_secret(), true);
        $iv = random_bytes(12);
        $tag = '';
        $ciphertext = openssl_encrypt(
            $payload,
            'aes-256-gcm',
            $key,
            OPENSSL_RAW_DATA,
            $iv,
            $tag,
            'ai-roundcube-v8-node',
            16
        );
        if ($ciphertext === false || strlen($tag) !== 16) {
            throw new RuntimeException('Chiffrement du nœud Nextcloud impossible.');
        }
        return $this->v8_b64url_encode("\x01" . $iv . $tag . $ciphertext);
    }

    private function v8_verify_node(string $token): array
    {
        $packed = $this->v8_b64url_decode($token);
        if (strlen($packed) < 30 || $packed[0] !== "\x01") {
            throw new RuntimeException('Nœud Nextcloud invalide.');
        }
        $iv = substr($packed, 1, 12);
        $tag = substr($packed, 13, 16);
        $ciphertext = substr($packed, 29);
        $payload_json = openssl_decrypt(
            $ciphertext,
            'aes-256-gcm',
            hash('sha256', $this->v8_node_secret(), true),
            OPENSSL_RAW_DATA,
            $iv,
            $tag,
            'ai-roundcube-v8-node'
        );
        if ($payload_json === false) throw new RuntimeException('Nœud Nextcloud non authentique.');
        $payload = json_decode($payload_json, true, 8, JSON_THROW_ON_ERROR);
        if (!is_array($payload) || (int) ($payload['exp'] ?? 0) < time()
            || !hash_equals((string) $this->rcmail->user->ID, (string) ($payload['user_id'] ?? ''))) {
            throw new RuntimeException('Nœud Nextcloud expiré ou non autorisé.');
        }
        $payload['path'] = $this->v8_assert_allowed_path((string) ($payload['path'] ?? ''));
        if (!in_array($payload['type'] ?? '', ['file', 'directory'], true)) {
            throw new RuntimeException('Type de nœud Nextcloud invalide.');
        }
        return $payload;
    }

    private function v8_webdav_url(array $config, string $path): string
    {
        return $config['base'] . '/remote.php/dav/files/' . rawurlencode($config['username']) . '/'
            . implode('/', array_map('rawurlencode', explode('/', $path)));
    }

    private function v8_webdav_request(
        array $config,
        string $method,
        string $url,
        array $headers,
        string $body = '',
        int $max_bytes = 2097152
    ): array {
        $response = '';
        $too_large = false;
        $curl = curl_init($url);
        if ($curl === false) throw new RuntimeException('Initialisation cURL impossible.');
        $options = [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => false,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT => $config['timeout'],
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_MAXREDIRS => 0,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_HTTPAUTH => CURLAUTH_BASIC,
            CURLOPT_USERPWD => $config['username'] . ':' . $config['password'],
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_WRITEFUNCTION => static function ($handle, string $chunk) use (&$response, &$too_large, $max_bytes): int {
                if (strlen($response) + strlen($chunk) > $max_bytes) {
                    $too_large = true;
                    return 0;
                }
                $response .= $chunk;
                return strlen($chunk);
            },
        ];
        if (defined('CURLOPT_PROTOCOLS') && defined('CURLPROTO_HTTPS')) {
            $options[CURLOPT_PROTOCOLS] = CURLPROTO_HTTPS;
        }
        curl_setopt_array($curl, $options);
        $ok = curl_exec($curl);
        $error = curl_error($curl);
        $status = (int) curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);
        if ($too_large) throw new RuntimeException('Réponse Nextcloud trop volumineuse.');
        if ($ok === false || $error !== '') throw new RuntimeException('Connexion Nextcloud impossible.');
        if ($status < 200 || $status >= 300) throw new RuntimeException('Nextcloud HTTP ' . $status);
        return ['status' => $status, 'body' => $response];
    }

    private function v8_webdav_list(array $config, string $path, int $depth): array
    {
        $path = $this->v8_assert_allowed_path($path);
        $xml = '<?xml version="1.0" encoding="UTF-8"?>'
            . '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:getlastmodified/>'
            . '<d:getcontentlength/><d:getcontenttype/><d:getetag/><d:resourcetype/></d:prop></d:propfind>';
        $raw = $this->v8_webdav_request(
            $config,
            'PROPFIND',
            $this->v8_webdav_url($config, $path),
            ['Depth: ' . $depth, 'Content-Type: application/xml; charset=UTF-8'],
            $xml
        )['body'];
        if ($depth === 0) return [];
        if (!class_exists('DOMDocument')) throw new RuntimeException('Extension PHP DOM absente.');
        $dom = new DOMDocument();
        if (!@$dom->loadXML($raw, LIBXML_NONET | LIBXML_NOBLANKS)) {
            throw new RuntimeException('Réponse WebDAV invalide.');
        }
        $xpath = new DOMXPath($dom);
        $xpath->registerNamespace('d', 'DAV:');
        $prefix = '/remote.php/dav/files/' . rawurlencode($config['username']) . '/';
        $deny = $this->rcmail->config->get('ai_nextcloud_denied_names', []);
        $deny = is_array($deny) ? array_map('mb_strtolower', $deny) : [];
        $extensions = $this->rcmail->config->get('ai_nextcloud_allowed_extensions', []);
        $extensions = is_array($extensions) ? array_map('mb_strtolower', $extensions) : [];
        $max_depth = max(1, min(20, (int) $this->rcmail->config->get('ai_nextcloud_browser_max_depth', 5)));
        $items = [];
        foreach ($xpath->query('//d:response') as $response) {
            $href = (string) $xpath->evaluate('string(d:href)', $response);
            $href_path = (string) parse_url($href, PHP_URL_PATH);
            $position = strpos($href_path, $prefix);
            if ($position === false) continue;
            $relative = $this->v8_normalize_path(substr($href_path, $position + strlen($prefix)));
            if ($relative === '' || $relative === $path) continue;
            try {
                $relative = $this->v8_assert_allowed_path($relative);
            } catch (Throwable $error) {
                continue;
            }
            $name = basename($relative);
            if (in_array(mb_strtolower($name), $deny, true)) continue;
            $is_directory = $xpath->query('.//d:resourcetype/d:collection', $response)->length > 0;
            if (!$is_directory && $extensions) {
                $extension = mb_strtolower(pathinfo($name, PATHINFO_EXTENSION));
                if (!in_array($extension, $extensions, true)) continue;
            }
            if ($is_directory) {
                $root = null;
                foreach ($this->v8_nextcloud_allowed_roots() as $candidate) {
                    if ($relative === $candidate || str_starts_with($relative, $candidate . '/')) {
                        $root = $candidate;
                        break;
                    }
                }
                if ($root !== null) {
                    $below = trim(substr($relative, strlen($root)), '/');
                    if ($below !== '' && count(explode('/', $below)) > $max_depth) continue;
                }
            }
            $type = $is_directory ? 'directory' : 'file';
            $items[] = [
                'name' => $name,
                'type' => $type,
                'size' => (int) $xpath->evaluate('string(.//d:getcontentlength)', $response),
                'content_type' => (string) $xpath->evaluate('string(.//d:getcontenttype)', $response),
                'etag' => trim((string) $xpath->evaluate('string(.//d:getetag)', $response), '"'),
                'modified' => (string) $xpath->evaluate('string(.//d:getlastmodified)', $response),
                'source_fingerprint' => hash_hmac('sha256', $relative, $this->v8_node_secret()),
                'node_id' => $this->v8_sign_node(
                    $relative,
                    $type,
                    trim((string) $xpath->evaluate('string(.//d:getetag)', $response), '"')
                ),
            ];
        }
        usort($items, static function (array $a, array $b): int {
            if ($a['type'] !== $b['type']) return $a['type'] === 'directory' ? -1 : 1;
            return strnatcasecmp($a['name'], $b['name']);
        });
        return array_slice($items, 0, 200);
    }

    private function v8_caldav_events(
        array $config,
        string $calendar_name,
        DateTimeImmutable $start,
        DateTimeImmutable $end
    ): array {
        $home = $config['base'] . '/remote.php/dav/calendars/' . rawurlencode($config['username']) . '/';
        $probe = '<?xml version="1.0" encoding="UTF-8"?>'
            . '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>';
        $raw = $this->v8_webdav_request(
            $config,
            'PROPFIND',
            $home,
            ['Depth: 1', 'Content-Type: application/xml; charset=UTF-8'],
            $probe
        )['body'];
        if (!class_exists('DOMDocument')) throw new RuntimeException('Extension PHP DOM absente.');
        $dom = new DOMDocument();
        if (!@$dom->loadXML($raw, LIBXML_NONET | LIBXML_NOBLANKS)) throw new RuntimeException('Réponse CalDAV invalide.');
        $xpath = new DOMXPath($dom);
        $xpath->registerNamespace('d', 'DAV:');
        $calendar_url = '';
        foreach ($xpath->query('//d:response') as $response) {
            $display = trim((string) $xpath->evaluate('string(.//d:displayname)', $response));
            if ($display === $calendar_name) {
                $href = (string) $xpath->evaluate('string(d:href)', $response);
                $href_parts = parse_url($href);
                $base_parts = parse_url($config['base']);
                if (!is_array($href_parts) || !is_array($base_parts)) {
                    throw new RuntimeException('URL CalDAV invalide.');
                }
                if (isset($href_parts['host'])) {
                    $href_port = (int) ($href_parts['port'] ?? 443);
                    $base_port = (int) ($base_parts['port'] ?? 443);
                    if (strtolower((string) ($href_parts['scheme'] ?? '')) !== 'https'
                        || !hash_equals(strtolower((string) $base_parts['host']), strtolower((string) $href_parts['host']))
                        || $href_port !== $base_port) {
                        throw new RuntimeException('Redirection CalDAV vers une autre origine refusée.');
                    }
                }
                $href_path = (string) ($href_parts['path'] ?? '');
                $calendar_prefix = '/remote.php/dav/calendars/' . rawurlencode($config['username']) . '/';
                if (!str_starts_with($href_path, $calendar_prefix) || isset($href_parts['query']) || isset($href_parts['fragment'])) {
                    throw new RuntimeException('Chemin CalDAV hors du compte autorisé.');
                }
                $calendar_url = $config['base'] . '/' . ltrim($href_path, '/');
                break;
            }
        }
        if ($calendar_url === '') throw new RuntimeException('Agenda Nextcloud autorisé introuvable.');
        $start_utc = $start->setTimezone(new DateTimeZone('UTC'))->format('Ymd\THis\Z');
        $end_utc = $end->setTimezone(new DateTimeZone('UTC'))->format('Ymd\THis\Z');
        $query = '<?xml version="1.0" encoding="UTF-8"?>'
            . '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            . '<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
            . '<c:comp-filter name="VEVENT"><c:time-range start="' . $start_utc . '" end="' . $end_utc . '"/>'
            . '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>';
        $raw = $this->v8_webdav_request(
            $config,
            'REPORT',
            $calendar_url,
            ['Depth: 1', 'Content-Type: application/xml; charset=UTF-8'],
            $query,
            4194304
        )['body'];
        $dom = new DOMDocument();
        if (!@$dom->loadXML($raw, LIBXML_NONET | LIBXML_NOBLANKS)) throw new RuntimeException('Résultat CalDAV invalide.');
        $xpath = new DOMXPath($dom);
        $xpath->registerNamespace('d', 'DAV:');
        $xpath->registerNamespace('c', 'urn:ietf:params:xml:ns:caldav');
        $events = [];
        foreach ($xpath->query('//d:response') as $response) {
            $ics = (string) $xpath->evaluate('string(.//c:calendar-data)', $response);
            if ($ics === '') continue;
            $parsed = $this->v8_parse_ics_event($ics);
            if ($parsed) $events[] = $parsed;
        }
        return $events;
    }

    private function v8_parse_ics_event(string $ics): ?array
    {
        $ics = preg_replace("/\r?\n[ \t]/", '', $ics);
        $values = [];
        foreach (preg_split('/\r?\n/', $ics) as $line) {
            if (!str_contains($line, ':')) continue;
            [$key, $value] = explode(':', $line, 2);
            $base = strtoupper(explode(';', $key, 2)[0]);
            if (in_array($base, ['UID', 'SUMMARY', 'LOCATION', 'DTSTART', 'DTEND'], true) && !isset($values[$base])) {
                $values[$base] = $value;
            }
        }
        if (empty($values['UID']) || empty($values['DTSTART'])) return null;
        $timezone = new DateTimeZone('Europe/Paris');
        $parse = static function (string $value) use ($timezone): ?DateTimeImmutable {
            $value = trim($value);
            $format = str_ends_with($value, 'Z') ? '!Ymd\THis\Z' : (str_contains($value, 'T') ? '!Ymd\THis' : '!Ymd');
            $zone = str_ends_with($value, 'Z') ? new DateTimeZone('UTC') : $timezone;
            $date = DateTimeImmutable::createFromFormat($format, $value, $zone);
            return $date ?: null;
        };
        $start = $parse((string) $values['DTSTART']);
        $end = isset($values['DTEND']) ? $parse((string) $values['DTEND']) : null;
        if (!$start) return null;
        return [
            'uid' => mb_substr((string) $values['UID'], 0, 300),
            'title' => mb_substr(str_replace(['\\,', '\\n'], [',', "\n"], (string) ($values['SUMMARY'] ?? '')), 0, 300),
            'location' => mb_substr((string) ($values['LOCATION'] ?? ''), 0, 300),
            'start' => $start->format(DateTimeInterface::ATOM),
            'end' => ($end ?: $start->modify('+30 minutes'))->format(DateTimeInterface::ATOM),
        ];
    }

    private function log_integration_error(string $prefix, Throwable $error): void
    {
        rcube::raise_error([
            'code' => 600,
            'type' => 'php',
            'message' => $prefix . ': ' . $error->getMessage(),
        ], true, false);
    }

    private function json_response(array $payload, int $status = 200): void
    {
        http_response_code($status);
        header('Content-Type: application/json; charset=UTF-8');
        header('Cache-Control: private, no-store, max-age=0');
        header('X-Content-Type-Options: nosniff');
        echo json_encode(
            $payload,
            JSON_UNESCAPED_UNICODE |
            JSON_UNESCAPED_SLASHES |
            JSON_THROW_ON_ERROR
        );
        exit;
    }

    private function json_error(string $message, int $status): void
    {
        $this->json_response(['error' => $message], $status);
    }

    private function safe_archive_filename(string $filename): string
    {
        $filename = trim($filename);
        $filename = preg_replace('/[\\x00-\\x1F\\x7F]+/u', '_', $filename);
        $filename = str_replace(['/', '\\', ':', '*', '?', '"', '<', '>', '|'], '_', $filename);
        $filename = trim($filename, ". \t\n\r\0\x0B");
        if ($filename === '') {
            $filename = 'piece-jointe';
        }
        return mb_substr($filename, 0, 180);
    }

    private function attachment_error(string $message, int $status): void
    {
        http_response_code($status);
        header('Content-Type: text/plain; charset=UTF-8');
        header('Cache-Control: no-store');
        echo $message;
        exit;
    }

    private function send_error(string $message): void
    {
        $this->rcmail->output->command('plugin.ai_error', $message);
        $this->rcmail->output->send();
        exit;
    }
}
