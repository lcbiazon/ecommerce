# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_auto_20161108_2101'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='enable_sdn_check',
            field=models.BooleanField(default=False, help_text='Enable SDN check at checkout.', verbose_name='Enable SDN check'),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='sdn_api_key',
            field=models.CharField(help_text='US Treasury SDN API key.', max_length=255, verbose_name='US Treasury SDN API key', blank=True),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='sdn_api_list',
            field=models.CharField(help_text='A comma seperated list of Treasury OFAC lists to check against.', max_length=255, verbose_name='SDN lists', blank=True),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='sdn_api_url',
            field=models.CharField(help_text='US Treasury SDN API URL.', max_length=255, verbose_name='US Treasury SDN API URL', blank=True),
        ),
    ]
