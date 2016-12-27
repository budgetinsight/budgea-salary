#!/usr/bin/env python3

from decimal import Decimal
from tempfile import mkstemp
from time import sleep
import argparse
import getpass
import os
import re
import subprocess
import sys

from termcolor import colored
import requests


class Employee(object):
    def __init__(self, data):
        self.salary = self.find_salary(data)
        self.iban = self.find_iban(data)
        self.name = self.find_name(data)
        self.period = self.find_period(data)

    def is_valid(self):
        return all([self.name, self.salary])

    def find_salary(self, data):
        m = re.search('Net \xe0? payer : ([\d\s\.,]+) euros', data)
        if m:
            return Decimal(m.group(1).replace(' ', '').replace(',', '.'))

    def find_iban(self, data):
        m = re.search('(FR\w\w \w\w\w\w \w\w\w\w \w\w\w\w \w\w\w\w \w\w\w\w \w\w\w)', data)
        if m:
            return m.group(1).replace(' ', '')

    def find_name(self, data):
        m = re.search('((Mademoiselle|Madame|Monsieur) ([^\)\r\n]+))', data)
        if m:
            return m.group(3)

    def find_period(self, data):
        m = re.search('([\wé]+ 20\d\d)', data)
        if m:
            return m.group(1)


class Application(object):
    baseurl = 'https://budgeapro.biapi.pro/2.0'
    username = 'romain@budget-insight.com'

    def __init__(self):
        self.token = None
        self.account_id = None
        self.parser = argparse.ArgumentParser(prog='budgea-salary')
        self.parser.add_argument('files', nargs='+', help='PDF files')
        self.parser.add_argument('-f', '--force', dest='force', action='store_true', help='Really execute transfers')

    def request(self, method, path, *args, **kwargs):
        if self.token:
            kwargs['headers'] = {'Authorization': 'Bearer %s' % self.token}

        return requests.request(method, self.baseurl + path, *args, **kwargs)

    def get(self, *args, **kwargs):
        return self.request('GET', *args, **kwargs)

    def post(self, *args, **kwargs):
        return self.request('POST', *args, **kwargs)

    def read_pdf(self, filename):
        outh, outname = mkstemp(suffix='.pdf')
        try:
            os.close(outh)

            subprocess.call(['mutool', 'clean', '-d', filename, outname])

            with open(outname, 'rb') as f:
                return f.read().decode('utf-8', 'ignore')
        finally:
            os.remove(outname)

    def do_transfer(self, employee, recipient):
        print('%s' % colored('YOLO...', 'green'))
        r = self.post('/users/me/accounts/%s/recipients/%s/transfers' % (self.account_id, recipient['id']),
                      data={'amount':   employee.salary,
                            'label':    'Salaire %s %s' % (employee.name.split(' ')[0], employee.period)
                           }).json()
        transfer_id = r['id']
        while True:
            r = self.post('/users/me/transfers/%s' % transfer_id,
                          data={'validated': int(self.args.force)}).json()
            if 'code' in r:
                if r['code'] == 'transferProcessing':
                    print('%s' % colored('Warning: user is locked. Waiting five seconds and retry...', 'yellow'))
                    sleep(5)
                    continue
                print('%s' % colored('Error: %s %s' % (r['code'], r.get('message', r.get('description', ''))), 'red'))
                return
            break
        print('%s State is %s' % (colored('Done!', 'green'), colored(r['state'], 'blue')))

    def main(self):
        self.args = self.parser.parse_args()
        if self.args.force:
            print('%s' % colored('Warning: you are in real mode, it will do real transfers!', 'yellow'))

        password = getpass.getpass('Please enter password for account %s: ' % colored(self.username, 'blue'))
        r = self.post('/auth/token',
                      data={'application':   'Android',
                            'username':      self.username,
                            'password':      password,
                            'scope':         'transfer'
                           })
        self.token = r.json()['token']

        r = self.get('/users/me/accounts?able_to_transfer=1').json()
        print('')
        for acc in r['accounts']:
            print('%s) %-60s %s' % (colored(acc['id'], 'red'),
                                    colored(acc['name'], 'yellow'),
                                    colored(acc['formatted_balance'], 'green')))
        print('From which account do you want to do transfers?', end=' ', flush=True)
        self.account_id = int(sys.stdin.readline().strip())
        if not self.account_id in [a['id'] for a in r['accounts']]:
            print('Error: %s is not a valid account.' % self.account_id, file=sys.stderr)
            return 1

        recipients = self.get('/users/me/accounts/%s/recipients' % self.account_id).json()['recipients']

        for filename in self.args.files:
            data = self.read_pdf(filename)
            employee = Employee(data)
            kind = ''
            if not employee.is_valid():
                data = self.post('/ocr', files={'file': ('lol.pdf', open(filename, 'rb'), 'application/pdf')}).json()['data']
                employee = Employee(data)
                kind = ' (OCRized)'
            if not employee.is_valid():
                print('\nError: Unable to parse file %s, skipping...' % colored(filename, 'yellow'), file=sys.stderr)
                continue

            print('')
            print('Filename: %s%s' % (colored(filename, 'yellow'), kind))
            print('Name: %s' % colored(employee.name, 'yellow'))
            print('IBAN: %s' % colored(employee.iban, 'yellow'))
            print('Amount: %s' % colored('%s €' % employee.salary, 'green'))
            print('Period: %s' % colored(employee.period, 'yellow'))
            for r in recipients:
                if r['category'] not in ('Salariés', 'Stagiaires'):
                    continue
                if employee.iban:
                    if r['iban'] == employee.iban:
                        break
                else:
                    for part in employee.name.split():
                        if part.lower() in r['label'].lower():
                            break
                    else:
                        continue
                    break
            else:
                print('Recipient: %s' % colored('UNABLE TO FIND IT, SKIPPING', 'red'))
                continue

            print('Recipient: %s (%s)' % (colored(r['label'], 'yellow'), r['category']))
            print('Recipient Bank: %s' % colored(r['bank_name'], 'yellow'))
            print('Recipient IBAN: %s' % colored(r['iban'], 'yellow'))
            print('Do you want to do transfer? (y/N)', end=' ', flush=True)
            if sys.stdin.readline().strip().lower() == 'y':
                self.do_transfer(employee, r)
            else:
                print(colored('Okay, skipping...', 'yellow'))


if __name__ == '__main__':
    try:
        sys.exit(Application().main())
    except KeyboardInterrupt:
        print('')
        sys.exit(1)
