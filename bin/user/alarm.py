# Copyright (c) 2017-2020 Graham Eddy <graham.eddy@gmail.com>
# Distributed under the terms of weewx's GNU Public License (GPLv3)
"""
alarm module provides weewx service that detects and acts upon alarm conditions

AlarmSvc: weewx service for alarms. at present, email is the only action taken
"""

import ast
import configobj
import getpass
import smtplib
from email.mime.text import MIMEText
import logging
import threading

import weewx
import weewx.units
from weewx.engine import StdService
from weeutil.weeutil import timestamp_to_string, to_bool

log = logging.getLogger(__name__)
version = "4.0.4"


class AlarmSvc(StdService):
    """
    service that detects and responds to alarm conditions

    * an alarm condition is when the specified rule (a python expression that
      includes one or more data_types, converted into specifed unit_system)
      evaluates to true. while true, the alarm is 'set', otherwise 'clear'
    * transitioning from 'clear' to 'set' or vice versa triggers the alarm
      to perform the associated action
      (the only actions defined are to send emails to a distribution list)
    * remaining either 'clear' or 'set' without transition does NOT trigger
    * alarms are only assessed at each report cycle (i.e. each ARCHIVE packet)

    weewx.conf configuration parameters:
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

    example of configuration via weewx.conf:
    [Alarms]
        #unit_system = METRIC
        server = mail.your_isp.com:25
        #user = ignored
        #password = ignored
        sender = "Wx Name <your_account@your_isp.au>"
        recipients = "Your Name <your_account@your_isp.com>", "Foo <bar@isp.com>"
        #text_set = "SET"
        #text_clear = "CLR"
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
    """

    def __init__(self, engine, config_dict):
        super(AlarmSvc, self).__init__(engine, config_dict)

        log.debug(f"{self.__class__.__name__}: starting (version {version})")
        if 'Alarms' not in config_dict:
            log.error(f"{self.__class__.__name__}: Alarms section not found")
            return      # slip away without becoming a packet listener

        mgr_sect = config_dict['Alarms']

        # unit system
        key = mgr_sect.get('unit_system', 'METRIC')
        if key not in weewx.units.unit_constants:
            log.error(f"{self.__class__.__name__}: invalid unit_system: {key}")
            return      # slip away without becoming a packet listener
        self.unit_system = weewx.units.unit_constants[key]

        # email service
        server = mgr_sect.get('server', 'localhost')
        user = mgr_sect.get('user', None)
        password = mgr_sect.get('password', None)
        sender = mgr_sect.get('sender', AlarmSvc.owner_emailaddr())
        mailer = Mailer(server, user, password, sender)

        # on_... sub-section defaults.
        # all default strings non-literal i.e. need to be ast.literal_eval'ed
        on_defaults = dict()
        key='recipients'; on_defaults[key] = mgr_sect.get(key, list())
        key='text_set'; on_defaults[key] = mgr_sect.get(key, "SET")
        key='text_clear'; on_defaults[key] = mgr_sect.get(key, "CLR")
        key='notify_first'; on_defaults[key] = mgr_sect.get(key, '').lower()
        key='subject_prefix'; on_defaults[key] = mgr_sect.get(key,
                                    r"Alarm [{_STATE}] ")
        key='subject'; on_defaults[key] = mgr_sect.get(key, r"{_NAME}")
        key='body_prefix'; on_defaults[key] = mgr_sect.get(key,
                                    r"Alarm:\t{_NAME}\nState:\t{_STATE}\n"
                                    r"Test:\t{_RULE}\nTime:\t{_TIME}\n")
        key='body'; on_defaults[key] = mgr_sect.get(key, r"")

        # create alarm definitions.
        # there is no particular relationship between or sequence of alarms
        alarm_defs_count = 0
        self.alarms = []
        for alarm_name, alarm_sect in mgr_sect.items():
            if isinstance(alarm_sect, configobj.Section):
                alarm_defs_count += 1
                alarm = self.parse_alarm(alarm_name, alarm_sect, on_defaults,
                                         mailer)
                if alarm:
                    self.alarms.append(alarm)

        # any work to do?
        if not self.alarms:
            log.error(f"{self.__class__.__name__}: not started"
                      f" (version {version}): no alarms")
            return      # slip away without becoming a packet listener

        # create 'stop' signal to threads
        self.stop = threading.Event()

        # start listening to new ARCHIVE packets
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
        log.info(f"{self.__class__.__name__}: started (version {version}):"
                 f" {len(self.alarms)} alarms"
                 f", {alarm_defs_count - len(self.alarms)} skipped")

    @staticmethod
    def owner_emailaddr():
        """return email address of owner of this weewx instance"""
        return getpass.getuser()    # don't bother with '@server'

    def parse_alarm(self, name, alarm_sect, defaults, mailer):
        """parse an alarm definition, returning Alarm instance or None"""

        if weewx.debug > 2:
            log.debug(f"{self.__class__.__name__}.parse_alarm"
                      f" name='{name}' alarm_sect='{alarm_sect}'")

        # rule
        rule = alarm_sect.get('rule', None)
        if not rule:
            log.warning(f"{self.__class__.__name__} [{alarm_name}] no rule")
            return None

        # on_... sub-sections
        sect = alarm_sect.get('on_set', None)
        on_true_params = self.parse_on_sect(sect, defaults, 'set') \
                         if sect is not None else None
        sect = alarm_sect.get('on_clear', None)
        on_false_params = self.parse_on_sect(sect, defaults, 'clear') \
                          if sect is not None else None

        return Alarm(name, rule, on_true_params, on_false_params, mailer)

    def parse_on_sect(self, on_sect, on_defaults, on_state):
        """parse an on_ sub-section in alarm definition"""

        # construct raw param dict
        params = {}
        for key in on_defaults:
            params[key] = on_sect[key] if key in on_sect else on_defaults[key]

        # transform global 'notify_first' list to local 'suppress_first' bool
        if 'suppress_first' in on_sect:
            # take local declaration
            try:
                params['suppress_first'] = to_bool(on_sect['suppress_first'])
            except ValueError:
                log.warning(f"{__class__.__name__}: invalid bool"
                             " suppress_first=\'{params['suppress_first']}\'"
                             " assumed False")
                params['suppress_first'] = False
        else:
            # take (reverse of) global opposite declaration
            params['suppress_first'] = on_state not in on_defaults['notify_first']
        del params['notify_first']  # remove extraneous parameter

        return params

    def new_archive_record(self, event):
        """handle ARCHIVE record by assessing all alarms against it"""

        # we can't unbind as a packet listener, but we can skip responses
        if self.stop.is_set():
            if weewx.debug > 0:
                log.debug(f"{self.__class__.__name__}.new_archive_record:"
                          f" stop.is_set")
            return

        def assess_all_alarms(packet):
            """assess all alarms against packet"""

            # convert packet to specified unit_system
            if weewx.debug > 1:
                log.debug(f"{self.__class__.__name__}.assess_all_alarms:"
                          f" ORIG packet={packet}")
            packet_cvt = weewx.units.to_std_system(packet, self.unit_system)
            if weewx.debug > 1:
                log.debug(f"{self.__class__.__name__}.assess_all_alarms:"
                          f" packet_cvt={packet_cvt}")

            # assess each alarm
            for alarm in self.alarms:
                if self.stop.is_set():
                    # service shutting down...
                    if weewx.debug > 0:
                        log.debug(f"{self.__class__.__name__}.assess_all_"
                                  f"alarms: stop.is_set")
                    break
                alarm.assess(packet_cvt)

        # spawn the assessment off into a thread to protect engine thread
        try:
            t = threading.Thread(target=assess_all_alarms, args=(event.record,))
            t.start()
        except threading.ThreadError as e:
            log.warning(f"{self.__class__.__name__}: failed to spawn assessment"
                        f" thread: {e.args[0]}")
        # assessment acts independently so don't wait for it to complete

    def shutDown(self):
        """respond to request for graceful shutdown"""

        log.info(f"{self.__class__.__name__}: shutdown")

        # no resources to release.
        # cannot unbind as listener

        # do best to stop threads
        self.stop.set()


class Alarm:
    """encapsulates an alarm, including its threshold and response to trigger"""

    def __init__(self, name, rule, on_true_params, on_false_params, mailer):

        self.name = name
        self.rule = rule        # TODO consider compiling the string
        self.on_true_params = on_true_params
        self.on_false_params = on_false_params
        self.mailer = mailer

        self.state = None       # start in unknown state

        if weewx.debug > 1:
            log.debug(f"{self.__class__.__name__} created: [{self.name}]"
                      f" rule='{self.rule}'"
                      f" on_true_params={self.on_true_params}"
                      f" on_false_params={self.on_false_params}"
                      f" mail={self.mailer} state={self.state}")

    @staticmethod
    def epoch_to_string(epoch):
        """convert epoch time to string"""
        return timestamp_to_string(epoch)[:19]

    def assess(self, packet_cvt):
        """assess alarm by evaluating its rule and triggering if its state has
           changed. if triggered, it performs associated action, if any"""

        # create evaluation context based on packet values plus the special
        # variables (_NAME, _RULE, _TIME).
        # note: special variable _STATE not known until rile has been eval'ed
        context = {**packet_cvt,
                   **{'_NAME': self.name, '_RULE': self.rule,
                      '_TIME': Alarm.epoch_to_string(packet_cvt['dateTime'])}}
        if weewx.debug > 2:
            log.debug(f"{self.__class__.__name__}.assess [{self.name}]"
                      f" context={context}")

        # evaluate rule to get new state
        new_state = self.eval_rule(context)
        if weewx.debug > 2:
            log.debug(f"{self.__class__.__name__}.assess [{self.name}]"
                      f" state={self.state}->{new_state}")
        if new_state is None:
            return      # no new state

        # have we changed state?
        old_state = self.state
        if new_state == old_state:
            return      # no state change
        self.state = new_state

        # get trigger parameters for new state
        params = self.on_true_params if new_state else self.on_false_params
        if weewx.debug > 1:
            log.debug(f"{self.__class__.__name__}.assess [{self.name}]"
                      f" params={params}")
        if not params:
            return      # no trigger defined

        # is this the first time we have state?
        if old_state is None:
            # first state. suppress notification of first state?
            if params['suppress_first']:
                return  # suppress notification of first state

        # start assembling the notification
        context['_STATE'] = params['text_set'] if new_state else \
                            params['text_clear']

        # recipients
        raw = params['recipients']
        if isinstance(raw, list):
            raw = ','.join(raw)
        recipients = self.eval_string(raw, {})
        if not recipients:
            log.warning(f"{self.__class__.__name__}.assess: [{self.name}]"
                        f" no recipients raw='{raw}'")
            return          # finished - no email

        # subject
        raw = params['subject_prefix'] + params['subject']
        subject = self.eval_string(raw, context)
        if subject is None:
            # fallback subject if garbled
            subject = f"{self.name} [{context['_STATE']}] *garbled* raw='{raw}'"

        # body
        raw = params['body_prefix'] + params['body']
        body = self.eval_string(raw, context)
        if body is None:
            # fallback body if garbled
            body = f"*garbled* raw='{raw}'"

        # send email
        if weewx.debug > 1:
            log.debug(f"{self.__class__.__name__}.assess: [{self.name}]"
                      f" recipients='{recipients}'"
                      f" subject='{subject}' body='{body}'")
        self.mailer.send(recipients, subject, body)

    def eval_rule(self, context):
        """evaluate rule. returns new state, or None if error"""

        new_state = None
        try:
            if weewx.debug > 1:
                log.debug(f"{self.__class__.__name__}.eval_rule [{self.name}]"
                          f" rule='{self.rule}'")
            new_state = eval(self.rule, {}, context)
            if weewx.debug > 1:
                log.debug(f"{self.__class__.__name__}.eval_rule [{self.name}]"
                          f" state={new_state}"
                          f" change?={self.state != new_state}")

        except NameError as e:
            # common mistake - referenced variable not in packet
            # so log something only if debug set
            if weewx.debug > 0:
                log.debug(f"{self.__class__.__name__} [{self.name}]"
                          f" rule: {e.args[0]}")
        except (ValueError, TypeError, KeyError) as e:
            # common mistake - bad use of a variable in packet
            # so log an error as this really shouldn't be allowed to happen
            log.warning(f"{self.__class__.__name__} [{self.name}]"
                        f" rule: {e.args[0]}")
        except Exception as e:
            # other errors shouldn't happen
            log.warning(f"{self.__class__.__name__} [{self.name}] rule: oops",
                        exc_info=e)

        return new_state

    def eval_string(self, raw, context):
        """evaluate raw string i.e. substitute variables and ast.literal_eval"""

        cooked = None
        try:
            if weewx.debug > 2:
                log.debug(f"{self.__class__.__name__}.eval_string:"
                          f" [{self.name}] raw='{raw}'")

            # substitute variables
            cooked = raw.format_map(context)
            if weewx.debug > 2:
                log.debug(f"{self.__class__.__name__}.eval_string:"
                          f" [{self.name}] substituted cooked='{cooked}'")

            # literal_eval
            cooked = ast.literal_eval("'" + cooked + "'")
            if weewx.debug > 2:
                log.debug(f"{self.__class__.__name__}.eval_string:"
                          f" [{self.name}] final cooked='{cooked}'")

        except NameError as e:
            # common mistake - referenced variable not in packet
            # so log something only if debug set
            if weewx.debug > 0:
                log.debug(f"{self.__class__.__name__} [{self.name}]"
                          f" {e.args[0]}")
        except (ValueError, TypeError, KeyError) as e:
            # common mistake - bad use of a variable in packet
            # so log an error as this really shouldn't be allowed to happen
            log.warning(f"{self.__class__.__name__} [{self.name}]"
                        f" {e.args[0]}")
        except Exception as e:
            # other errors shouldn't happen
            log.warning(f"{self.__class__.__name__} [{self.name}] oops",
                        exc_info=e)

        return cooked


class Mailer:
    """knows how to send email messages"""

    def __init__(
            self,
            server,         # hostname of SMTP server
            user,           # ignored
            password,       # ignored
            sender          # email address of sender
    ):

        self.server = server
        self.user = user            # not used
        self.password = password    # not used
        self.sender = sender

        if weewx.debug > 1:
            log.debug(f"{self.__class__.__name__} created:"
                      f" server={self.server} user={self.user}"
                      f" password={self.password} sender={self.sender}")

    def send(self, recipients, subject, body):

        # compose email
        envelope = MIMEText(f'{body}\n')
        envelope['Subject'] = subject
        envelope['From'] = self.sender
        envelope['To'] = recipients
        if weewx.debug > 1:
            log.debug(f"{self.__class__.__name__}.send: envelope='{envelope}'")

        # send it via relay. assumes no authentication required
        smtp = None
        try:
            smtp = smtplib.SMTP(self.server)
            smtp.sendmail(
                    envelope['From'], envelope['To'], envelope.as_string())
            log.info(f"{self.__class__.__name__}: sent: {subject}")
        except smtplib.SMTPException as e:
            log.error(f"{self.__class__.__name__}:"
                      f": SMTP send failed: {e.args[0]}: {subject}")
        finally:
            if smtp:
                smtp.quit()

