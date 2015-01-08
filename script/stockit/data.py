#! /usr/bin/python
# coding=utf-8

import sys
import os
import cPickle
import logging
import random
import numpy as np
import gc

import common
from .records import fundRecords, hdRecords
from .function import *

__all__ = ['PrepareReocrds']


class PrepareReocrds(object):
    '''
    从原始数据中按code整理数据，code下的数据按日期排列从近到久远
    '''
    def __init__(self, config = common.config):
        self.__config = config
        self.__merged_dict = {}
        self.__records_file = open(self.__config.records_file, 'w')

    
    def __del__(self):
        if  self.__records_file:
            self.__records_file.close()
    

    def merge(self):
        #加载原始数据
        input_filename = self.__config.raw_records_file
        prev_k = None
        record_list = []
        for line in open(input_filename):
            record = line[:-1]
            items = record.split('\t')
            k = items[0]
            if prev_k != k:
                if prev_k is not None:
                    self.__mergeCodeRecords(record_list)
                record_list = []
            prev_k = k
            record_list.append(items)

        self.__mergeCodeRecords(record_list)


    def __adjustMa(self, records):
        '''
        对价格复权处理
        '''
        rate = 1.0
        prev_price = None
        ma_gradient = None
        for idx in range(len(records)):
            items = records[idx]
            price = items[2]
            new_rate = self.__adjustRate(price, prev_price, ma_gradient)
            if prev_price:
                logging.debug('%s:%f:%f:%f' % (str(items[:2]), price, prev_price, new_rate))
            rate *= new_rate
            items[2] = round(items[2]*rate, 3)
            ma_gradient = items[3]
            prev_price = price


    def __adjustRate(self, price, prev_price, ma_gradient):
        '''
        计算复权比例
        '''
        if prev_price is None:
            return 1
        ma_gradient = 1 + (float(ma_gradient)/100)
        org_price = prev_price / ma_gradient
        rate = org_price / price
        rate = round(rate, 2)
        return rate


    def __mergeCodeRecords(self, records):
        prev_k = None
        record_list = []
        code_records = []
        for idx in range(len(records)):
            items = records[idx]
            k,v = self.__getRawKV(items)
            if prev_k != k:
                merged_res = self.__mergeRecords(record_list)
                if merged_res:
                    code_records.append(merged_res)
                record_list = []
            record_list.append(v)
            prev_k = k

        self.__saveCodeRecords(code_records)


    def __saveCodeRecords(self, records):
        if records is None or len(records) == 0:
            return
        code = records[0][0]
        #根据date排序
        sorted_data = sorted(records, reverse=True)
        #复权
        self.__adjustMa(sorted_data)
        self.__records_file.write(str(sorted_data) + '\n')


    def __getRawKV(self, items):
        code, cate, date = items[:3]
        key = code + date
        v = []
        class_name = cate + "Records"
        aClass = eval(class_name)
        if aClass:
            aObj = aClass()
            v = aObj.processItems( items)
        
        res = items[:3]
        res.extend(v)
        return key,res


    def __mergeRecords(self, records):
        local_dict = {}
        for items in records:
            code, cate, date = items[:3]
            local_dict[cate] = items[3:]
        fund_data = local_dict.get('fund', fundRecords().getDefaultRecords())
        hd_data = local_dict.get('hd', hdRecords().getDefaultRecords())
        if not fund_data:
            return None

        res = [code, date]
        res.extend(fund_data)
        res.extend(hd_data)
        return res


    def __mergeSaveRecords(self, records):
        result = self.__mergeRecords(records)
        if result is None:
            return
        code = records[0][0]
        value = self.__merged_dict.get(code, [])
        value.append(result)
        self.__merged_dict[code] = value



class StockRecordData():
    '''
    对于某一record下处理好的数据，按照给定的处理函数，抽取特征
    '''
    def __init__(self, config = common.config()):
        self.__config = config


    def extractFeatures(self, feat_processors):
        in_filename = self.__config.records_file
        for line in open(in_filename):
            line = line[:-1]
            code_records = eval(line)
            self.extractCodeRecords(code_records, feat_processors)
        for feat_processor in feat_processors:
            feat_processor.finish()


    def extractCodeRecords(self, records, feat_processors):
        code = records[0][0]
        data_records = [ele[2:] for ele in records]
        dates = [ele[1] for ele in records]
        nu_records = np.asarray(data_records)
        for beg in range(len(records)):
            label_end = beg + self.__config.label_window
            feat_end = label_end + self.__config.feat_window
            if feat_end > len(records):
                # 忽略长度不够的reocrds
                break
            date = dates[label_end]
            self.extractBasicFeatures(nu_records[label_end:feat_end],
                    nu_records[beg:label_end + 1],
                    code,
                    date,
                    feat_processors)
    
        if len(records) >= self.__config.feat_window:
            date = dates[0]
            self.extractBasicFeatures(nu_records[:self.__config.feat_window],
                    None,
                    code,
                    date,
                    feat_processors)


    def extractBasicFeatures(self, records, label_records, code, date, feat_processors):
        label = None
        if label_records is not None:
            # 过滤label不满足要求的样本
            # train data 不允许label为None
            label = self.getLabel(label_records)
            if label is None:
                return
        # label用来区分train和test数据
        for feat_processor in feat_processors:
            #feat_processor.processRecords(records, code, date, label)
            feat_processor.putRecords(records, code, date, label)


    def getLabel(self, label_records):
        ma_idx = common.recordsFiledMap['ma']
        ma_records = label_records[:,ma_idx]
        cur_ma = ma_records[-1]
        max_ma = np.max(ma_records[:-1])
        min_ma = np.min(ma_records[:-1])
        max_gradient = (max_ma - cur_ma) / cur_ma
        min_gradient = (min_ma - cur_ma) / cur_ma

        if max_gradient < 0.1 and min_gradient < 0 and min_gradient < self.__config.lower_thredhold:
            return round(min_gradient, 4)

        if max_gradient > self.__config.upper_threohold:
            return round(max_gradient, 4)


class RecordsSampler(object):
    def __init__(self, config=common.config):
        self.__config = config


    def sample(self):
        pos_num = 0
        neg_num = 0
        fin = open(self.__config.sample_file_in)
        for line in fin.readlines():
            items = line[:-1].split('\t')
            if float(items[2]) > 0:
                pos_num += 1
            else:
                neg_num += 1
        fin.close()

        ratio = pos_num / float(neg_num)
        fin = open(self.__config.sample_file_in)
        fout = open(self.__config.sample_file_out, 'w')
        for line in fin.readlines():
            items = line[:-1].split('\t')
            if float(items[2]) < 0:
                random_num = random.random()
                if random_num < ratio:
                    continue
            fout.write(line)
        fin.close()
        fout.close()

