#!/bin/python3

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.


# there is C#-code style

import os
import sys
import subprocess
import argparse
from datetime import datetime
from dataclasses import dataclass, field
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from typing import no_type_check


def RunCommand(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(args=command, shell=True, encoding="utf-8", capture_output=True)


def WriteToFile(string: str, path: str):
    tmpfile = open(path, "w", encoding = "utf-8");
    tmpfile.write(string);
    tmpfile.close()


@dataclass(frozen=True)
class IdsOfVM: # indentificators of virtual machine
    name:str = ""
    uuid:str = ""
    macAddress: list[str] = field(default_factory=list) 
    hddPaths: list[str] = field(default_factory=list)

    @classmethod
    def init2(cls, vmxml: str):# -> IdsOfVM:
        """ Альтернативный конструктор IdsOfVM. 

            Возможны исключения при парсинге XML (AttributeError).
        """

        @no_type_check # Проверять None каждому узлу слишком заморочно, а try не спасает от жалоб проверяльщиков синтаксиса.
        def _init2_IdsOfVM(vmxml):
            root: Element = ElementTree.fromstring(vmxml)    

            result_name: str = ""
            result_uuid: str = ""
            result_macAddress: list[str] = []
            result_hddPaths: list[str] = []

            try: 
                result_name = root.find("./name").text
                result_uuid = root.find("./uuid").text
                
                netInterfaces: list[Element] = root.findall("./devices/interface")
                for i in netInterfaces:
                    result_macAddress.append(i.find("./mac").get("address"))
    
                hdds: list[Element] = GetHdds_fromVM(root)
                for i in hdds:
                    result_hddPaths.append(i.find("./source").get("file"))

            except Exception as e:
                e.add_note("Ошибка в XML виртуальной машины")
                raise e

            return (result_name, result_uuid, result_macAddress, result_hddPaths)

        return cls(*_init2_IdsOfVM(vmxml))


def _GetHdds(rootXml: Element, xmlpath: str) -> list[Element]:
    disks: list[Element] = rootXml.findall(xmlpath) # не все диски - hdd
    hdds: list[Element] = list(filter(lambda disk: (disk.get("type") == "file") and (disk.get("device") == "disk"), disks))
    return hdds
def GetHdds_fromVM  (rootXml: Element) -> list[Element]: return _GetHdds(rootXml,        "./devices/disk")
def GetHdds_fromSnap(rootXml: Element) -> list[Element]: return _GetHdds(rootXml, "./domain/devices/disk")


def Replace_IdsVM_InSnaps(snapshotXml: str, vm_ids: IdsOfVM) -> str:

    @no_type_check
    def _Replace_IdsVM_InSnaps(snapshotXml, vm_ids):
        root: Element = ElementTree.fromstring(snapshotXml)
        try:
            root.find("./domain/name").text = vm_ids.name
            root.find("./domain/uuid").text = vm_ids.uuid
            
            for i in range(0, len(vm_ids.macAddress)):
                root.find("./domain/devices/interface[" + str(i + 1) + "]/mac").set("address", vm_ids.macAddress[i])

            hdds:list[Element] = GetHdds_fromSnap(root)
            for ihdd, ipath  in list(zip(hdds, vm_ids.hddPaths, strict=True)):
                ihdd.find("./source").set("file", ipath)

        except Exception as e:
            e.add_note("Ошибка в XML снимка")
            raise e

        return ElementTree.tostring(root, encoding="unicode")

    return _Replace_IdsVM_InSnaps(snapshotXml, vm_ids)

def CloneSnapshot(source_VM_name: str, snapshotName: str, targetVM_Ids: IdsOfVM):
    snapshotXML: str = RunCommand("virsh snapshot-dumpxml " + source_VM_name + " " + snapshotName + " --security-info").stdout
    
    resultXML: str = Replace_IdsVM_InSnaps(snapshotXML, targetVM_Ids)

    snapshotFile = "/tmp/" + source_VM_name + "-" + snapshotName + "-" + datetime.now().strftime("%Y%m%d%H%M%S%f") + ".xml"
    WriteToFile(resultXML, snapshotFile)    
    RunCommand("virsh snapshot-create " + targetVM_Ids.name + " " + snapshotFile + " --redefine")
    os.remove(snapshotFile)


def CloneSnapshots_Recursively(source_VM_name: str, targetVM_Ids: IdsOfVM, snapshotName: str | None = None):
    # Проблема в том, что снимки нельзя перенести линейным списком. Нужно соблюсти иерархию.
    # Быстрый способ1 - перенасти по id снимков. Получить их из qemu-info snapshot. Но в виртуалке может быть несколько дисков. Можно найти главный в загрузке. Но т.к. в снимках храниться и состояние ВМ, то нет уверенности, что где-то не был сменён приоритет загрузки.
    # Быстрый способ2 - перенасти по дате снимков. virsh snapshot-list VMname. Но средний столбец - дата. Нет уверенности, что её формат не зависит от настроек ОС.
    # Поэтому, путь будет не простой вариант -  с прохождением дерева. Зато больше уверенности в правильности переноса.
    
    isStartRecursion = snapshotName is None
    middle_cmd = " --roots " if isStartRecursion else " --from " + str(snapshotName)
    cmd = "virsh snapshot-list " + source_VM_name + middle_cmd + " | sed -e '1,2d' | sed -e '$d' | awk '{print $1 }'"

    snapshs_ofThisLevel: list[str] = RunCommand(cmd).stdout.splitlines()

    for i in snapshs_ofThisLevel:
        CloneSnapshot(source_VM_name, i, targetVM_Ids)
        CloneSnapshots_Recursively(source_VM_name, targetVM_Ids, i)


def Main(source_VM_name: str, target_VM_name: str, isQuiet: bool = False) -> int:
    # Ручной алгоритм копирования снимков:
    # 1) Клонировать виртуалку (без диска)
    # 2) Скопировать диск (cp), и заменить в клонированной виртуалке путь на него
    # 3) Получить список снимков
    #    sudo virsh snapshot-list debian11_nogui-clone
    # 4) Рекурсивно(иначе потомки не примутся без родителя) выгрузить каждый снимок в xml, поправить id(name ВМ, uuid ВМ, mac адреса, source дисков) и экспортировать в клон ВМ.
    #    sudo virsh snapshot-dumpxml имяВиртуалкиИсточника имяСниска --security-info > /tmp/имяСнимка.xml
    #    sudo virsh snapshot-create имяВиртуалкиКлона /tmp/имяСнимка.xml --redefine
        
    # Прога сделана для автоматизации этапов 3, 4.
    
    # И предполагается что виртуалка обычная - с одним или несколькими жёсткими дисками. 
    # Не знаю, какая должна быть конфигурация виртуалки, чтобы не сработала замена id устройств.
    

    targetVM_Ids = IdsOfVM.init2(RunCommand("virsh dumpxml " + target_VM_name).stdout)

    CloneSnapshots_Recursively(source_VM_name, targetVM_Ids)
    
    # Проверка корректности переноса снимков
    treeSourceVM: str = RunCommand("virsh snapshot-list " + source_VM_name + " --tree").stdout
    treeTargetVM: str = RunCommand("virsh snapshot-list " + target_VM_name + " --tree").stdout
    result = (treeSourceVM == treeTargetVM)
    if not isQuiet: 
        if result: print ("Clone snapshots is successfully")
        else:      print ("Clone snapshots is wrong")
    
    return 0 if result else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clone snapshots from VM to VM. Works for a copied qcow, no cloned.")
    parser.add_argument('source_VM_name', type=str)
    parser.add_argument('target_VM_name', type=str)
    parser.add_argument('--quiet', action="store_false", help="write messages")
    args = parser.parse_args()

    source_VM_name: str = args.source_VM_name
    target_VM_name: str = args.target_VM_name
    isQuietMode: bool = args.quiet

    sys.exit(Main(args.source_VM_name, args.target_VM_name, args.quiet))
