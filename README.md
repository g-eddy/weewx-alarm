<h1>weewx-alarm</h1>
<p>service that detects and responds to alarm conditions</p>

* an alarm condition is when the specified rule (a python expression that
  includes one or more data_types, converted into specifed unit_system)
  evaluates to true
* transitioning from true to false or vice versa triggers the alarm
  to perform the associated action
  (the only actions defined are to send emails to a distribution list)
* remaining either true or false without transition does NOT trigger
* the trigger is re-armed when the alarm condition is no longer met,
  so it can fire again at next transition
* alarms are only assessed in report cycle (at each ARCHIVE packet)

<p>weewx.conf configuration parameters:</p>
<pre>
  [Alarms]
    unit_system     units system for text produced, including for emails:
                    one of US, METRIC or METRICWX (default: METRIC)
    server          email relay host (default: 'localhost')
    user            not implemented (ignored)
    password        not implemented (ignored)
    sender          apparent email sender (default: user owner of weewx)
    recipients      default list of notification email recipients
                    (default: none)
    subject         default notification email subject line, as a format
                    string evaluated in the context of the packet
                    converted to the specified unit system - it supports
                    the '{var}' syntax to substitutes variables.
                    special variables are defined:
                        _NAME   alarm name
                        _RULE   rule (python expression) performed
                        _TIME   timestamp of packet
                    (default: '{_NAME}' - see prefix)
    prefix          default prefix to all subject lines, a format string
                    (default: "Alarm: " - see subject)
    body            default body of email notification, a format string
                    same as for 'subject' (default:
                    'Alarm: {_NAME}\\nRule: {_RULE}\\nTime: {_TIME}\\n')
   
alarm definition - many can be defined
    [[_alarm_name_]]
        rule        expression that returns true if the alarm condition
                    is met, otherwise false. it is evaluated in the
                    context of the current packet converted to the defined
                    unit_system, so can include data_types, literals and
                    _builtins_ functions
        # on transition from false to true
        [[[on_true]]]
            recipients  overrides default email list if present
            subject     overrides default subject if present
            prefix      overrides default prefix if present
            body        overrides default body of present. very useful
                        for including specific data_type values in format
                        string
        # on transition from true to false
        [[[on_false]]]  
            recipients
            subject
            prefix
            body
</pre>

<p>example of configuration via weewx.conf:</p>
<pre>
  [Alarms]
    #unit_system = METRIC
    server = mail.your_isp.com:25
    #user = ignored
    #password = ignored
    sender = "Wx Name <your_account@your_isp.au>"
    recipients = "Your Name <your_account@your_isp.com>", "Foo <bar@isp.com>"
    #subject = "{_NAME}"
    prefix = "!! "
    #body = "Alarm: {_NAME}\\nTest: {_TEST}\\nTime: {_TIME}\\n"
    [[Hot]]
        rule = "outTemp >= 30.0"    # 30 C
        [[[on_true]]]
            #recipients = default_to_Alarm.mail_to
            #subject = default_to_Alarm.mail_subject
            body = "Alarm: {_NAME}\\noutTemp: {outTemp}\\nTime: {_TIME}\\n"
    [[Very Hot]]
        rule = "outTemp >= 37.8"    # 100 F
        [[[on_true]]]
            body = "Alarm: {_NAME}\\noutTemp: {outTemp}\\nTime: {_TIME}\\n"
    [[Freezing]]
        rule = "outTemp >= 0.0"     # 0 C
        [[[on_false]]]
            prefix = ""
            subject = "Brrrr! {_NAME}"
            body = "Alarm: {_NAME}\\noutTemp: {outTemp}\\nTime: {_TIME}\\n"
    [[River Temp Battery LOW]]
        rule = "int(txBatteryStatus) & 0x02"    # bit#1 of mask set
        [[[on_true]]]
            recipients = "Your Name <your_account@your_isp.com>", "Batteries <hardware@shop.com"
            prefix = "Order: "
            body = "Please provide 4xAAA batteries\\n"
        [[[on_false]]]
            subject = "River Temp Battery okay"
            body = "Alarm: {_NAME}: CLEARED\\n"
</pre>
