import csv

with open("train.csv", encoding="utf-8") as f, \
     open("ground_truth.txt", "w", encoding="utf-8") as out:
    reader = csv.reader(f)
    count = 0
    for row in reader:
        if len(row) >= 3:
            text = row[2].strip()
            if text:
                out.write(text + "\n")
                count += 1
                if count >= 20:
                    break