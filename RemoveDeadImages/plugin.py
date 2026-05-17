#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import re
from lxml import etree

def run(bk):
    """
    安全删除所有 XHTML/HTML 中 src 指向不存在图片的 <img> 标签。
    完全保留 XML 声明、DOCTYPE、文档结构，不产生多余实体。
    """
    # 1. 收集现存图片文件名（basename）
    present_images = set()
    for img_id, opf_href, mime in bk.image_iter():
        basename = opf_href.split('/')[-1]
        present_images.add(basename)

    if not present_images:
        print("No images present in the book.")
        return 0

    removed_count = 0
    for html_id, href in bk.text_iter():
        # 读取原始内容
        data = bk.readfile(html_id)
        if isinstance(data, bytes):
            data_str = data.decode('utf-8')
        else:
            data_str = data

        # 统一换行符，防止后续序列化产生 &#13;
        data_str = data_str.replace('\r\n', '\n').replace('\r', '\n')

        # 2. 提取前导声明（<?xml?> 与 <!DOCTYPE>），保存原样
        xml_decl = ''
        doctype_decl = ''
        rest = data_str

        m = re.match(r'\s*(<\?xml[^?]*\?>)\s*', rest, re.DOTALL)
        if m:
            xml_decl = m.group(1)
            rest = rest[m.end():]

        m = re.match(r'\s*(<!DOCTYPE[^>]*>)\s*', rest, re.DOTALL)
        if m:
            doctype_decl = m.group(1)
            rest = rest[m.end():]

        # 3. 用 XML 解析器解析（保持原结构）
        try:
            root = etree.fromstring(rest.encode('utf-8'))
        except etree.XMLSyntaxError as e:
            print(f"XML parse error in {href}: {e}, skip.")
            continue

        # 4. 查找所有 img 元素（忽略命名空间）
        imgs = root.xpath('//*[local-name()="img"]')
        dirty = False
        for img in imgs:
            src = img.get('src', '')
            if not src:
                img.getparent().remove(img)
                dirty = True
                continue

            # 提取文件名（去掉路径和查询参数）
            src_basename = src.split('/')[-1].split('?')[0]
            if src_basename not in present_images:
                img.getparent().remove(img)
                dirty = True
                removed_count += 1
                print(f"Removed <img> with src='{src}' in {href}\n")

        if dirty:
            # 5. 序列化回 XHTML 字符串
            new_body = etree.tostring(root, encoding='unicode', method='xml',
                                      with_tail=True, xml_declaration=False)
            # 重新拼接声明
            parts = []
            if xml_decl:
                parts.append(xml_decl)
            if doctype_decl:
                parts.append(doctype_decl)
            parts.append(new_body)
            bk.writefile(html_id, '\n'.join(parts))

    print(f"Done. Removed {removed_count} dead image tag(s).")
    return 0