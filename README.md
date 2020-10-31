<h1>weewx-alarm</h1>
<p>service that detects and responds to alarm conditions</p>
<p>status: released</p>

* an alarm condition is when the specified rule (a python expression that
  includes one or more data_types, converted into specifed unit_system)
  evaluates to true. while true, the alarm is 'set', otherwise 'clear'
* transitioning from 'clear' to 'set' or vice versa triggers the alarm
  to perform the associated action
  (the only actions defined are to send emails to a distribution list)
* remaining either 'clear' or 'set' without transition does NOT trigger
* alarms are only assessed at each report cycle (i.e. each ARCHIVE packet)

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
    text_set        value representing SET state (default: "SET")
    text_clear      value representing CLEAR state (default: "CLR")
    notify_first    on startup, notify of first state detected if that
                    state is listed here i.e. 'clear', 'set', 'clear,set'
                    or none (default: none)
    subject         default notification email subject line, as a format
                    string evaluated in the context of the packet
                    converted to the specified unit system - it supports
                    the '{var}' syntax to substitutes variables.
                    special variables are defined:
                        _NAME   alarm name
                        _RULE   rule (python expression) performed
                        _STATE  set or cleared value - see {state_set} and
                                {state_clear} for allowed values
                        _TIME   timestamp of packet
                    (default: '{_NAME}')
    subject_prefix  default prefix to all subject lines, a format string
                    (default: "Alarm [{_STATE}] " - see {subject})
    body            default body of email notification, a format string
                    same as for {subject} (default:
                    'Alarm:\t{_NAME}\nState:\t{_STATE}\nRule:\t{_RULE}\n'+
                    'Time:\t{_TIME}\n')
    body_prefix     default prefix to all email bodies, a format string
                    (default: "" - see {subject})
   
    # alarm definition - many can be defined
    [[_alarm_name_]]
        rule        expression that returns true if the alarm condition
                    is met, otherwise false. it is evaluated in the
                    context of the current packet converted to the defined
                    unit_system, so can include data_types, literals and
                    _builtins_ functions
        # on transition from false to true
        [[[on_set]]]
            recipients  overrides default {recipients} if present
            text_set    overrides default {text_set} if present
            text_clear  overrides default {text_clear} if present
            suppress_first
                        overrides default {notify_first} if present (bool)
            subject     overrides default {subject} if present
            subject_prefix
                        overrides default {subject_prefix} if present
            body        overrides default {body} if present. very useful
                        for including specific data_type values in format
                        string
            body_prefix overrides default {body_prefix} if present
        # on transition from true to false
        [[[on_clear]]]
            recipients
            text_set
            text_clear
            suppress_first
            subject
            subject_prefix
            body
            body_prefix


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
    notify_first = set
    #subject = "{_NAME}"
    #subject_prefix = "Alarm [{_STATE}] "
    subject_prefix = "!{_STATE}! "
    #body_prefix = "Alarm:\t{_NAME}\nState:\t{_STATE}\nTest:\t{_RULE}\n"+
    #              "Time:\t{_TIME}\n"
    #body = ""
    [[Hot]]
        rule = "outTemp >= 30.0"    # 30 C
        [[[on_set]]]
            #recipients = _default_to_Alarm.recipients_
            #subject = _default_to_Alarm.subject_
            body = "outTemp:\t{outTemp}\n"
    [[Very Hot]]
        rule = "outTemp >= 37.8"    # 100 F
        [[[on_set]]]
    [[Freezing]]
        rule = "outTemp <= 0.0"     # 0 C
        [[[on_set]]]
            suppress_first = true
            subject_prefix = ""
            subject = "Brrrr! {_NAME}"
            body = "outTemp:\t{outTemp}\n"
    [[River Temp Battery LOW]]
        rule = "int(txBatteryStatus) & 0x02"    # bit#1 of mask set
        [[[on_set]]]
            recipients = "Your Name <your_account@your_isp.com>",\
                         "Batteries <hardware@shop.com"
            subject_prefix = "Order: "
            body_prefix = "Please provide 4xAAA batteries\n"
        [[[on_clear]]]
            subject = "River Temp Battery okay"
            #body = "Alarm: {_NAME}: CLEARED\n"
</pre>
