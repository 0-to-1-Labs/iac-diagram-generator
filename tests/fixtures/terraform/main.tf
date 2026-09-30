# Small Terraform fixture: references, depends_on, data source, count, for_each.
data "aws_ami" "al2" {
  most_recent = true
  owners      = ["amazon"]
}

variable "db_password" {
  type    = string
  default = "hunter2-default"
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "private" {
  count      = 2
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.${count.index}.0/24"
}

resource "aws_security_group" "DbSG" {
  vpc_id = aws_vpc.main.id
  # a comment mentioning aws_instance.web should not create an edge
}

resource "aws_instance" "web" {
  ami           = data.aws_ami.al2.id
  subnet_id     = aws_subnet.private[0].id
  instance_type = "t3.micro"
  depends_on    = [aws_subnet.private]

  tags = {
    Name = "web-${var.environment}"
  }
}

resource "aws_db_instance" "Main-DB" {
  for_each               = toset(["a"])
  vpc_security_group_ids = [aws_security_group.DbSG.id]
  password               = var.db_password
  master_password        = "literal-secret"
}
