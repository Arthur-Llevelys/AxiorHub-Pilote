<?php

/** A single, non-mutating link from Roundcube to the cabinet mail agent. */
class axiorhub_mail_agent extends rcube_plugin
{
    public $task = 'mail|settings|addressbook|calendar';

    private const AGENT_URL = 'https://courriel.example.com/agent-courriel/accueil';

    public function init()
    {
        $rcmail = rcmail::get_instance();
        $this->register_action('plugin.axiorhub_mail_agent_open', [$this, 'open_agent']);
        $this->include_script('axiorhub_mail_agent.js');
        $this->include_stylesheet('axiorhub_mail_agent.css');
        $rcmail->output->set_env('axiorhub_mail_agent_url', self::AGENT_URL, true);
        $this->add_button([
            'command'  => 'plugin.axiorhub_mail_agent_open',
            'type'     => 'link',
            'label'    => 'Agent IA',
            'title'    => 'Ouvrir l’agent IA du cabinet',
            'class'    => 'button-agent-ia',
            'classact' => 'button-agent-ia selected',
            'innerclass' => 'button-inner',
        ], 'taskbar');
    }

    public function open_agent()
    {
        header('Cache-Control: no-store');
        header('Location: ' . self::AGENT_URL, true, 302);
        exit;
    }
}
